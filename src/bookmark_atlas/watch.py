"""Directed author collection; bookmarks and each rule have separate memberships."""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime, timedelta

from .adapters.x_api import API
from .adapters.x_api import parse_page as api_page
from .adapters.x_web import parse_page as web_page
from .adapters.x_web import walk
from .config import atomic_write
from .http import json_body, request
from .models import (
    AtlasError,
    AuthError,
    Page,
    ParseError,
    RateLimitError,
    UnavailableError,
    digest,
)
from .sync import sync_one


def read_rules(home):
    path = home / "watchlists.json"
    if not path.exists():
        return {}
    try:
        values = json.loads(path.read_text())
        if not isinstance(values, dict):
            raise ValueError()
        return {name: validate_rule(name, rule) for name, rule in values.items()}
    except (OSError, ValueError, TypeError) as exc:
        raise AtlasError("watchlists.json 无效，请检查规则配置。") from exc


def validate_rule(name, rule):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", name):
        raise AtlasError("规则名使用小写字母、数字和连字符，最多 40 位。")
    defaults = dict(
        authors=[],
        following=False,
        keywords=[],
        days=7,
        kinds=["post", "quote"],
        language="",
        max_authors=20,
        max_pages=5,
        enabled=True,
    )
    if not isinstance(rule, dict) or rule.keys() - defaults.keys():
        raise AtlasError("定向采集规则含未知字段。")
    rule = defaults | rule
    for field in ("following", "enabled"):
        if type(rule[field]) is not bool:
            raise AtlasError(f"{field} 必须为布尔值。")
    for field, maximum in [("days", 365), ("max_authors", 100), ("max_pages", 100)]:
        if type(rule[field]) is not int or not 1 <= rule[field] <= maximum:
            raise AtlasError(f"{field} 超出范围。")
    if not isinstance(rule["authors"], list) or not all(
        isinstance(a, str) and re.fullmatch(r"[A-Za-z0-9_]{1,15}", a) for a in rule["authors"]
    ):
        raise AtlasError("作者名应为不带 @ 的 X 用户名。")
    if not rule["authors"] and not rule["following"]:
        raise AtlasError("请指定作者或选择关注列表。")
    if (
        not isinstance(rule["keywords"], list)
        or len(rule["keywords"]) > 50
        or not all(isinstance(k, str) and 0 < len(k.strip()) <= 200 for k in rule["keywords"])
    ):
        raise AtlasError("关键词应为非空短文本数组。")
    if (
        not isinstance(rule["kinds"], list)
        or not rule["kinds"]
        or any(k not in ("post", "quote") for k in rule["kinds"])
    ):
        raise AtlasError("当前采集类型可选 post（原创）/ quote（引用）。")
    if not isinstance(rule["language"], str) or not re.fullmatch(r"[a-z-]{0,12}", rule["language"]):
        raise AtlasError("语言应为短语言代码，例如 en、zh。")
    return rule


def save_rule(home, name, rule):
    rules = read_rules(home)
    rules[name] = validate_rule(name, rule)
    atomic_write(home / "watchlists.json", json.dumps(rules, ensure_ascii=False, indent=2))
    return {"name": name, **rules[name]}


class Transport:
    def __init__(self, base):
        self.base = base
        self.name = base.name

    def identity(self):
        return self.base.identity()

    def close(self):
        self.base.close()

    def graphql(self, operation, variables):
        body = self.base._graphql(operation, variables)
        if body.get("errors"):
            # Reuse the error classifier, without treating an error as an empty roster.
            web_page(body)
        return body

    @staticmethod
    def user_value(value):
        username = value.get("core", {}).get("screen_name") or value.get("legacy", {}).get(
            "screen_name"
        )
        if not value.get("rest_id") or not username:
            raise ParseError("X 作者响应缺少 ID 或用户名。")
        return {"id": str(value["rest_id"]), "username": username}

    def user(self, username):
        if self.name == "api":
            body = json_body(
                request(self.base.client, "GET", f"{API}/users/by/username/{username}")
            )
            user = body.get("data", {})
            if body.get("errors") or not user.get("id") or not user.get("username"):
                raise ParseError("官方 API 无法解析指定作者。")
            return user
        body = self.graphql(
            "UserByScreenName", {"screen_name": username, "withSafetyModeUserFields": True}
        )
        return self.user_value(body.get("data", {}).get("user", {}).get("result", {}))

    def following(self, cursor=None):
        owner = self.identity()
        if self.name == "api":
            params = {"max_results": 1000, "user.fields": "username"}
            if cursor:
                params["pagination_token"] = cursor
            body = json_body(
                request(
                    self.base.client,
                    "GET",
                    f"{API}/users/{owner.account_id}/following",
                    params=params,
                )
            )
            if body.get("errors") or ("data" not in body and "meta" not in body):
                raise ParseError("官方 API 关注列表响应不完整。")
            users = body.get("data", [])
            if not isinstance(users, list) or not all(
                u.get("id") and u.get("username") for u in users
            ):
                raise ParseError("关注列表中存在无效作者。")
            return users, body.get("meta", {}).get("next_token")
        variables = {"userId": owner.account_id, "count": 50, "includePromotedContent": False}
        if cursor:
            variables["cursor"] = cursor
        body = self.graphql("Following", variables)
        groups = [
            n["instructions"]
            for n in walk(body.get("data", {}))
            if isinstance(n.get("instructions"), list)
        ]
        if not groups:
            raise ParseError("未找到关注列表，不能将未知结构当成空列表。")
        users = {}
        next_cursor = None
        for group in groups:
            for instruction in group:
                entries = instruction.get("entries", []) + (
                    [instruction["entry"]] if "entry" in instruction else []
                )
                for entry in entries:
                    content = entry.get("content", {})
                    if content.get("cursorType") == "Bottom":
                        next_cursor = content.get("value")
                    containers = [content.get("itemContent", {})] + [
                        i.get("item", {}).get("itemContent", {}) for i in content.get("items", [])
                    ]
                    for container in containers:
                        if "user_results" not in container:
                            continue
                        value = container["user_results"].get("result", {})
                        if value.get("__typename") in ("UserUnavailable", "UserTombstone"):
                            continue
                        user = self.user_value(value)
                        users[user["id"]] = user
        return list(users.values()), next_cursor

    def posts(self, user, cursor, start):
        if self.name == "api":
            params = {
                "max_results": 100,
                "start_time": start.isoformat().replace("+00:00", "Z"),
                "tweet.fields": "created_at,author_id,entities,attachments,note_tweet,lang,referenced_tweets",
                "expansions": "author_id,attachments.media_keys",
                "user.fields": "username",
                "media.fields": "type,url,preview_image_url,variants",
            }
            if cursor:
                params["pagination_token"] = cursor
            body = json_body(
                request(self.base.client, "GET", f"{API}/users/{user['id']}/tweets", params=params)
            )
            return api_page(body)
        variables = {
            "userId": user["id"],
            "count": 40,
            "includePromotedContent": False,
            "withQuickPromoteEligibilityTweetFields": True,
            "withVoice": True,
        }
        if cursor:
            variables["cursor"] = cursor
        return web_page(self.graphql("UserTweets", variables))


class AuthorStream:
    def __init__(self, transport, user, rule, start):
        self.transport, self.user, self.rule, self.start = transport, user, rule, start
        self.name = transport.name

    def identity(self):
        return self.transport.identity()

    def page(self, cursor=None):
        page = self.transport.posts(self.user, cursor, self.start)
        if cursor and page.next_cursor == cursor and page.items:
            raise ParseError("作者时间线重复游标；本页未推进。")
        matches = []
        stamps = []
        for item in page.items:
            try:
                stamp = datetime.fromisoformat(item.published_at.replace("Z", "+00:00"))
            except (ValueError, TypeError) as exc:
                raise ParseError("作者推文缺少可验证的发布时间。") from exc
            if stamp.tzinfo is None:
                raise ParseError("作者推文时间缺少时区。")
            stamps.append(stamp)
            if stamp < self.start or item.kind not in self.rule["kinds"]:
                continue
            if item.author_id != self.user["id"]:
                continue  # ignore unrelated thread context
            if self.rule["language"] and item.language != self.rule["language"]:
                continue
            text = (item.text + " " + " ".join(item.links)).casefold()
            if self.rule["keywords"] and not any(
                k.casefold() in text for k in self.rule["keywords"]
            ):
                continue
            matches.append(item)
        # A lone pinned post is not a chronological boundary.
        ended_by_time = len(stamps) > 1 and all(s < self.start for s in stamps)
        return Page(matches, None if ended_by_time else page.next_cursor, page.raw)


def roster(transport, rule, sleep=time.sleep):
    if not rule["following"]:
        return [transport.user(a) for a in dict.fromkeys(rule["authors"])]
    users = {}
    cursor = None
    seen = set()
    for index in range(100):
        if index:
            sleep(2)
        values, nxt = transport.following(cursor)
        for user in values:
            users[user["id"]] = user
        if not nxt or (not values and nxt == cursor):
            selected = list(users.values())
            if rule["authors"]:
                wanted = {a.casefold() for a in rule["authors"]}
                selected = [u for u in selected if u["username"].casefold() in wanted]
            return selected
        if nxt in seen:
            raise ParseError("关注列表分页游标重复。")
        seen.add(nxt)
        cursor = nxt
    raise AtlasError("关注列表仍未遍历完；本轮未开始作者采集，请缩小到指定作者。")


def run_watches(
    store, home, factories, *, mode="auto", preferred="api", name=None, sleep=time.sleep
):
    rules = read_rules(home)
    if name and name not in rules:
        raise AtlasError("找不到指定规则。")
    output = {"added": 0, "updated": 0, "authors": 0, "rules": []}
    for title, rule in rules.items():
        if (name and name != title) or not rule["enabled"]:
            continue
        if store.cooldown("x") > time.time():
            raise RateLimitError(store.cooldown("x"))
        order = [mode] if mode != "auto" else [preferred] + [k for k in factories if k != preferred]
        transport = None
        for pos, key in enumerate(order):
            try:
                transport = Transport(factories[key]())
                store.bind_owner(transport.identity())
                users = roster(transport, rule, sleep)
                break
            except (AuthError, UnavailableError):
                if transport:
                    transport.close()
                    transport = None
                if pos == len(order) - 1:
                    raise
            except BaseException as exc:
                if isinstance(exc, RateLimitError):
                    store.set_cooldown("x", exc.retry_at)
                if transport:
                    transport.close()
                raise
        fingerprint = digest(rule)[:16]
        rotation = f"watch-rotation:{title}:{fingerprint}"
        offset = int(store.checkpoint("x", rotation) or 0)
        selected = (
            users[offset : offset + rule["max_authors"]]
            if offset < len(users)
            else users[: rule["max_authors"]]
        )
        if offset >= len(users):
            offset = 0
        start = datetime.now(UTC) - timedelta(days=rule["days"])
        result = {
            "name": title,
            "roster": len(users),
            "processed": 0,
            "remaining_authors": max(0, len(users) - offset - len(selected)),
            "partial": False,
        }
        try:
            for user in selected:
                collection = f"watch:{title}:{fingerprint}:{user['id']}"
                stream = AuthorStream(transport, user, rule, start)
                run = sync_one(
                    store, stream, max_pages=rule["max_pages"], collection=collection, sleep=sleep
                )
                output["added"] += run["added"]
                output["updated"] += run["updated"]
                output["authors"] += 1
                result["processed"] += 1
                result["partial"] |= run["status"] == "partial"
                offset += 1
                with store.db:
                    store.db.execute(
                        "INSERT INTO checkpoints VALUES(?,?,?) ON CONFLICT(site,adapter) DO UPDATE SET cursor=excluded.cursor",
                        ("x", rotation, str(offset if offset < len(users) else 0)),
                    )
                sleep(2)
            output["rules"].append(result)
        finally:
            transport.close()
    return output
