from __future__ import annotations

import contextlib
import html
import json
import os
import re
from collections import deque
from datetime import UTC, datetime
from urllib.parse import unquote, urljoin, urlparse

import httpx

from ..http import json_body, network_client, request
from ..models import AuthError, Identity, Item, Page, ParseError, RateLimitError, UnavailableError


def browser_cookies(browser: str = "chrome", cookie_file: str | None = None) -> dict:
    try:
        import browser_cookie3
    except ImportError as exc:
        raise AuthError("网页入口需要 browser 扩展：uv sync --extra browser。") from exc
    if browser not in ("chrome", "firefox", "brave", "edge", "chromium", "quark"):
        raise AuthError("不支持的浏览器，请使用 chrome/firefox/brave/edge/chromium/quark。")
    try:
        if browser == "quark":
            from pathlib import Path

            file = cookie_file or str(
                Path.home() / "Library/Application Support/Quark/Default/Cookies"
            )
            reader = browser_cookie3.ChromiumBased(
                browser="Quark",
                cookie_file=file,
                domain_name="x.com",
                osx_key_service="Quark Safe Storage",
                osx_key_user="Quark",
            )
            jar = reader.load()
        else:
            jar = getattr(browser_cookie3, browser)(cookie_file=cookie_file, domain_name="x.com")
        values = {
            c.name: c.value
            for c in jar
            if c.domain.lstrip(".") == "x.com" and c.name in ("auth_token", "ct0", "twid")
        }
    except Exception as exc:
        raise AuthError(f"无法读取 {browser} 的 X 登录态；检查浏览器配置和系统密钥访问。") from exc
    if not values.get("auth_token") or not values.get("ct0"):
        raise AuthError(f"{browser} 中没有可用的 X 登录态，请先登录 x.com。")
    return values


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def article_body(value: dict) -> tuple[str, str, list[dict]]:
    """Title, body and images of a long-form X Article attached to a tweet.

    An article-carrying post has only a t.co link in full_text, so the article
    is the whole content. The GraphQL request already asks for it
    (withArticlePlainText); this reads what that returns.
    """
    result = (value.get("article") or {}).get("article_results", {}).get("result") or {}
    body = (result.get("plain_text") or "").strip()
    if not body:
        return "", "", []
    media = [
        {"type": "photo", "media_url_https": url}
        for entity in result.get("media_entities", [])
        if (url := (entity.get("media_info") or {}).get("original_img_url"))
    ]
    return (result.get("title") or "").strip(), body, media


def quoted_post(value: dict) -> tuple[str, str, list[dict]]:
    """Author, text and images of the post this one quotes.

    A quote-tweet's own words are often a one-line endorsement; the substance
    is in the post being quoted, which the timeline returns but we never read.
    Each image is marked `quoted`, so the archive keeps them apart from the
    bookmarker's own media all the way to the rendered report.
    """
    result = (value.get("quoted_status_result") or {}).get("result") or {}
    while result.get("__typename") == "TweetWithVisibilityResults":
        result = result.get("tweet", {})
    legacy = result.get("legacy") or {}
    note = (result.get("note_tweet") or {}).get("note_tweet_results", {}).get("result") or {}
    text = note.get("text") or legacy.get("full_text") or ""
    title, body, article_media = article_body(result)
    if body:
        text = "\n\n".join(part for part in (title, body) if part)
    user = (result.get("core") or {}).get("user_results", {}).get("result") or {}
    author = user.get("core", {}).get("screen_name") or user.get("legacy", {}).get(
        "screen_name", ""
    )
    if not text.strip():
        return "", "", []
    media = [
        {**entry, "quoted": True}
        for entry in article_media + (legacy.get("extended_entities") or {}).get("media", [])
    ]
    return author, html.unescape(text).strip(), media


def parse_tweet(value: dict) -> Item | None:
    while value.get("__typename") == "TweetWithVisibilityResults":
        value = value.get("tweet", {})
    if value.get("__typename") in ("TweetUnavailable", "TweetTombstone"):
        return None
    legacy = value.get("legacy", {})
    item_id = value.get("rest_id") or legacy.get("id_str")
    if not item_id or "full_text" not in legacy:
        raise ParseError("网页推文结构发生变化；已停止本页写入。")
    user = value.get("core", {}).get("user_results", {}).get("result", {})
    username = user.get("core", {}).get("screen_name") or user.get("legacy", {}).get(
        "screen_name", ""
    )
    note = value.get("note_tweet", {}).get("note_tweet_results", {}).get("result", {})
    entities = note.get("entity_set") or legacy.get("entities", {})
    stamp = legacy.get("created_at", "")
    with contextlib.suppress(ValueError):
        stamp = datetime.strptime(stamp, "%a %b %d %H:%M:%S %z %Y").astimezone(UTC).isoformat()
    title, body, article_media = article_body(value)
    text = html.unescape(note.get("text") or legacy["full_text"])
    if body:
        # A link-only post carries nothing of its own; keep any real words it has.
        own = re.sub(r"https://t\.co/\w+", "", text).strip()
        text = "\n\n".join(part for part in (own, title, body) if part)
    quoted_author, quoted_text, quoted_media = quoted_post(value)
    if quoted_text:
        # Marked, so the archive never presents someone else's words as the
        # bookmark's own.
        text = f"{text}\n\n【引用 @{quoted_author}】\n{quoted_text}"
    return Item(
        "x",
        str(item_id),
        text,
        f"https://x.com/i/status/{item_id}",
        kind="repost"
        if legacy.get("retweeted_status_result")
        else "reply"
        if legacy.get("in_reply_to_status_id_str")
        else "quote"
        if legacy.get("is_quote_status")
        else "post",
        language=legacy.get("lang", ""),
        author_id=user.get("rest_id") or legacy.get("user_id_str", ""),
        author=username,
        published_at=stamp,
        links=[
            u.get("expanded_url") or u["url"]
            for u in entities.get("urls", [])
            if u.get("expanded_url") or u.get("url")
        ],
        media=legacy.get("extended_entities", {}).get("media", []) + article_media + quoted_media,
    )


def parse_page(body: dict) -> Page:
    if body.get("errors"):
        codes = {x.get("code") for x in body["errors"] if isinstance(x, dict)}
        if codes & {32, 89, 215, 326}:
            raise AuthError("X 网页会话已失效或账号需要验证。")
        if 88 in codes:
            import time

            raise RateLimitError(time.time() + 900)
        raise ParseError("X GraphQL 返回错误；请更新网页元数据或检查会话。")
    instructions = [
        n["instructions"]
        for n in walk(body.get("data", {}))
        if isinstance(n.get("instructions"), list)
    ]
    if not instructions:
        raise ParseError("未找到收藏时间线；不能将未知响应当作空收藏。")
    items = {}
    cursor = None
    for group in instructions:
        for instruction in group:
            for entry in instruction.get("entries", []) + (
                [instruction["entry"]] if "entry" in instruction else []
            ):
                content = entry.get("content", {})
                if content.get("cursorType") == "Bottom":
                    cursor = content.get("value")
                # Only timeline item containers are memberships; quoted tweets are not.
                containers = [content.get("itemContent", {})]
                containers += [
                    i.get("item", {}).get("itemContent", {}) for i in content.get("items", [])
                ]
                for container in containers:
                    if "tweet_results" in container:
                        post = parse_tweet(container["tweet_results"].get("result", {}))
                        if post:
                            items[post.item_id] = post
    return Page(list(items.values()), cursor, body)


def metadata_from_script(script: str) -> tuple[str | None, dict[str, dict]]:
    bearer = re.search(r"AAAAA[A-Za-z0-9%_\-]{40,}", script)
    operations = {}
    for match in re.finditer(
        r'queryId\s*:\s*["\x27]([^"\x27]+)["\x27]\s*,\s*operationName\s*:\s*["\x27]([^"\x27]+)',
        script,
    ):
        tail = script[match.end() : match.end() + 6500]
        feature_match = re.search(r"featureSwitches\s*:\s*\[([^\]]*)\]", tail)
        features = (
            re.findall(r'["\x27]([^"\x27]+)["\x27]', feature_match[1]) if feature_match else []
        )
        operations[match[2]] = {"query_id": match[1], "features": features}
    return unquote(bearer[0]) if bearer else None, operations


def identity_from_html(source: str) -> Identity:
    marker = re.search(r"window\.__INITIAL_STATE__\s*=\s*", source)
    if not marker:
        raise AuthError("X 页面未提供登录身份；请确认浏览器已登录且账号无需验证。")
    try:
        state, _ = json.JSONDecoder().raw_decode(source[marker.end() :])
        account_id = str(state["session"]["user_id"])
        if not account_id.isdigit():
            raise ValueError("invalid account ID")
        user = state.get("entities", {}).get("users", {}).get("entities", {}).get(account_id, {})
        username = user.get("screen_name", "")
        return Identity("x", account_id, username)
    except (ValueError, KeyError, TypeError) as exc:
        raise AuthError("X 当前页面没有有效的登录账号，无法绑定本地归档。") from exc


def discover(client: httpx.Client, required=None) -> tuple[str, dict, Identity]:
    required = set(required or ["Bookmarks"])
    response = request(client, "GET", "https://x.com/i/bookmarks")
    source = response.text
    identity = identity_from_html(source)
    urls = re.findall(r'(?:src|href)=["\x27]([^"\x27]+\.js(?:\?[^"\x27]*)?)["\x27]', source)
    queue = deque(urljoin("https://x.com", u) for u in urls)
    # Webpack lazy chunks are described by separate name/hash maps in the bootstrap.
    # Prefer bookmark chunks; do not execute remote JavaScript.
    for match in re.finditer(
        r'(\d+):"([^"\n]*(?:Bookmarks|UserProfile|Profile|FollowLists|Following)[^"\n]*)"', source
    ):
        hashes = re.findall(r"\b" + match[1] + r':"([0-9a-f]{16})"', source)
        if hashes:
            queue.append(
                f"https://abs.twimg.com/responsive-web/client-web/{match[2]}.{hashes[-1]}a.js"
            )
    seen = set()
    bearer = os.getenv("ATLAS_X_WEB_BEARER")
    operations = {}
    # No cookies or authorization headers are ever sent to asset hosts.
    with network_client() as assets:
        while queue and len(seen) < 40:
            url = queue.popleft()
            if url in seen or urlparse(url).hostname != "abs.twimg.com":
                continue
            seen.add(url)
            response = assets.get(url)
            if response.status_code == 404:
                continue  # An optional import is not a collection failure.
            if response.status_code != 200:
                raise UnavailableError(f"X 公开脚本加载失败（HTTP {response.status_code}）。")
            script = response.text
            token, found = metadata_from_script(script)
            bearer = bearer or token
            operations.update(found)
            if bearer and required.issubset(operations):
                return bearer, operations, identity
            imports = re.findall(
                r'["\x27]((?:\./|https://abs\.twimg\.com/)[^"\x27\s]+\.js)["\x27]', script
            )
            queue.extend(urljoin(url, u) for u in imports)
    if bearer and required.issubset(operations):
        return bearer, operations, identity
    raise UnavailableError(
        "无法从 X 当前网页发现所需接口；网页入口需适配当前版本，可改用官方 API。"
    )


class XWeb:
    name = "web"

    def __init__(
        self,
        browser="chrome",
        cookie_file=None,
        *,
        client: httpx.Client | None = None,
        cookies: dict | None = None,
        metadata: tuple | None = None,
    ):
        self.client = client or network_client()
        if cookies is None:
            auth, csrf = os.getenv("ATLAS_X_AUTH_TOKEN"), os.getenv("ATLAS_X_CT0")
            if bool(auth) != bool(csrf):
                raise AuthError("ATLAS_X_AUTH_TOKEN 与 ATLAS_X_CT0 必须同时配置。")
            cookies = (
                {"auth_token": auth, "ct0": csrf, "twid": os.getenv("ATLAS_X_TWID", "")}
                if auth and csrf
                else browser_cookies(browser, cookie_file)
            )
        self._cookies = cookies
        for name, value in cookies.items():
            self.client.cookies.set(name, value, domain="x.com", path="/")
        self.client.headers.update(
            {
                "x-csrf-token": cookies["ct0"],
                "x-twitter-auth-type": "OAuth2Session",
                "x-twitter-active-user": "yes",
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://x.com/i/bookmarks",
            }
        )
        self._metadata = metadata
        self._identity = None

    def _prepare(self):
        if not self._metadata:
            self._metadata = discover(self.client)
        self.client.headers["Authorization"] = f"Bearer {self._metadata[0]}"

    def identity(self):
        if self._identity:
            return self._identity
        self._prepare()
        # The authenticated HTML bootstrap is server-provided, not a user-ID guess
        # from a locally editable cookie. This also avoids obsolete v1.1 endpoints.
        self._identity = self._metadata[2]
        return self._identity

    def _graphql(self, operation: str, variables: dict) -> dict:
        self._prepare()
        if operation not in self._metadata[1]:
            token, operations, identity = discover(self.client, [operation])
            if identity.account_id != self._metadata[2].account_id:
                from ..models import OwnerMismatch

                raise OwnerMismatch("发现接口时登录账号发生变化，已停止采集。")
            self._metadata = (token, self._metadata[1] | operations, identity)
            self.client.headers["Authorization"] = f"Bearer {token}"
        info = self._metadata[1][operation]
        features = {name: True for name in info.get("features", [])}
        features.update(
            {
                "responsive_web_graphql_exclude_directive_enabled": True,
                "responsive_web_enhance_cards_enabled": False,
            }
        )
        url = f"https://x.com/i/api/graphql/{info['query_id']}/{operation}"
        return json_body(
            request(
                self.client,
                "GET",
                url,
                params={
                    "variables": json.dumps(variables),
                    "features": json.dumps(features),
                    "fieldToggles": json.dumps(
                        {"withArticleRichContentState": True, "withArticlePlainText": True}
                    ),
                },
            )
        )

    def page(self, cursor=None):
        self.identity()
        variables = {"count": 20, "includePromotedContent": False}
        if cursor:
            variables["cursor"] = cursor
        return parse_page(self._graphql("Bookmarks", variables))

    def close(self):
        self.client.close()
