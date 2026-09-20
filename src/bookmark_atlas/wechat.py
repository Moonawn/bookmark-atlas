"""WeChat article intake and replaceable RSS discovery, without login scraping."""

from __future__ import annotations

import contextlib
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from .config import atomic_write
from .models import AtlasError, Identity, Item, Page, ParseError, now

ARTICLE_HOST = "mp.weixin.qq.com"
IMAGE_HOSTS = {"mmbiz.qpic.cn", "mmbiz.qlogo.cn"}
MAX_BODY = 12 * 1024 * 1024


class VerificationRequired(AtlasError):
    pass


def article_url(value):
    """Reject non-article URLs and strip tracking without dropping article identity."""
    p = urlsplit(html.unescape(value.strip()))
    if p.scheme not in ("http", "https") or p.hostname != ARTICLE_HOST or p.username or p.port:
        raise ParseError("需要 mp.weixin.qq.com 的公众号文章链接。")
    if not re.fullmatch(r"/s(?:/[A-Za-z0-9_-]+)?", p.path):
        raise ParseError("链接不是公众号文章页。")
    query = parse_qs(p.query)
    kept = {k: query[k][0] for k in ("__biz", "mid", "idx", "sn") if query.get(k)}
    if p.path == "/s" and not all(kept.get(k) for k in ("__biz", "mid", "idx")):
        raise ParseError("公众号长链接缺少文章标识。")
    return urlunsplit(("https", ARTICLE_HOST, p.path, urlencode(kept), ""))


class Node:
    def __init__(self, tag="", attrs=None):
        self.tag, self.attrs, self.children = tag, attrs or {}, []
        self.closed = False

    def text(self):
        if self.tag in ("script", "style", "noscript"):
            return ""
        return "".join(c.text() if isinstance(c, Node) else c for c in self.children)


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.ids, self.meta = {}, {}
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if key := node.attrs.get("id"):
            self.ids[key] = node
        if tag == "meta":
            self.meta[node.attrs.get("property") or node.attrs.get("name")] = node.attrs.get(
                "content", ""
            )
        if tag not in {
            "img",
            "meta",
            "link",
            "br",
            "hr",
            "input",
            "source",
            "wbr",
            "embed",
            "area",
        }:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack[i].closed = True
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_article(source, url, expected_biz=""):
    doc = Document(source)
    content = doc.ids.get("js_content")
    if content is None:
        if any(s in source for s in ("环境异常", "访问过于频繁", "wappoc_appmsgcaptcha")):
            raise VerificationRequired(
                "公众号触发环境验证；本轮停止访问，请在浏览器完成验证后再试。"
            )
        raise ParseError("未获取到公众号正文；可能已删除、受限或页面结构变化，不能按空文章入库。")
    if not content.closed:
        raise ParseError("公众号正文 HTML 不完整，保留待重试。")

    def variable(name):
        # WeChat commonly emits `var biz = '' || 'actual-id'`. Read literal
        # alternatives only; never evaluate JavaScript from an article.
        literal = r"(?:'[^'\n]*'|\"[^\"\n]*\")"
        match = re.search(
            r"\bvar\s+"
            + re.escape(name)
            + r"\s*=\s*("
            + literal
            + r"(?:\s*\|\|\s*"
            + literal
            + r")*)",
            source,
        )
        if not match:
            return ""
        values = re.findall(r"['\"]([^'\"]*)['\"]", match[1])
        return next((html.unescape(v) for v in values if v), "")

    canonical = article_url(doc.meta.get("og:url") or url)
    params = parse_qs(urlsplit(canonical).query)
    biz = variable("biz") or params.get("__biz", [""])[0]
    mid = variable("mid") or params.get("mid", [""])[0]
    idx = variable("idx") or params.get("idx", [""])[0]
    if not biz or not mid.isdigit() or not idx.isdigit():
        raise ParseError("正文缺少稳定账号或文章标识，暂不入库以避免重复。")
    if expected_biz and biz != expected_biz:
        raise ParseError("文章账号与订阅的公众号不一致，已停止收录。")
    title = (doc.meta.get("og:title") or doc.ids.get("activity-name", Node()).text()).strip()
    account = doc.ids.get("js_name", Node()).text().strip() or variable("nickname")
    if not title or not account:
        raise ParseError("公众号文章缺少标题或账号信息。")
    links, media = [], []

    def render(node):
        if isinstance(node, str):
            return node
        if node.tag in ("script", "style", "noscript"):
            return ""
        if node.tag == "img":
            image = html.unescape(node.attrs.get("data-src") or node.attrs.get("src") or "")
            if image.startswith("//"):
                image = "https:" + image
            if urlsplit(image).hostname in IMAGE_HOSTS and urlsplit(image).scheme == "https":
                media.append({"type": "photo", "media_url_https": image})
            return "\n[图片见本地附件]\n"
        if node.tag in ("iframe", "video", "audio", "mpvoice", "mp-video"):
            return "\n[内嵌音视频未转写；正文不能替代媒体内容]\n"
        text = "".join(render(c) for c in node.children)
        if node.tag == "a":
            href = html.unescape(node.attrs.get("href") or "")
            if urlsplit(href).scheme in ("https", "http"):
                links.append(href)
                text += f" ({href})"
        if node.tag in {
            "p",
            "div",
            "section",
            "h1",
            "h2",
            "h3",
            "h4",
            "li",
            "tr",
            "blockquote",
            "pre",
        }:
            return "\n" + text + "\n"
        return "\n" if node.tag == "br" else text

    text = re.sub(r"\n[ \t]*\n+", "\n\n", render(content)).replace("\xa0", " ").strip()
    if len(content.text().strip()) < 20:
        raise ParseError("正文过短或主要为媒体，需人工核对，未标记完整。")
    published = ""
    stamp = variable("ct") or variable("create_time")
    if stamp.isdigit():
        with contextlib.suppress(ValueError, OSError, OverflowError):
            published = datetime.fromtimestamp(int(stamp), UTC).isoformat()
    # Use the stable long URL when possible, so short/scene links share a hash.
    sn = variable("sn") or params.get("sn", [""])[0]
    canonical = article_url(
        "https://mp.weixin.qq.com/s?" + urlencode({"__biz": biz, "mid": mid, "idx": idx, "sn": sn})
    )
    return Item(
        "wechat",
        f"{biz}:{mid}:{idx}",
        f"{title}\n\n{text}",
        canonical,
        author_id=biz,
        author=account,
        published_at=published,
        links=list(dict.fromkeys(links)),
        media=list({m["media_url_https"]: m for m in media}.values()),
        language="zh",
        kind="article",
    )


def get_text(client, url, *, article=False):
    """Bound responses and redirects; article fetches never leave WeChat."""
    for _ in range(5):
        if article:
            url = article_url(url)
        try:
            with client.stream("GET", url) as response:
                if response.is_redirect:
                    target = urljoin(url, response.headers.get("location", ""))
                    if "wappoc_appmsgcaptcha" in target:
                        raise VerificationRequired(
                            "公众号需要环境验证；本轮停止访问，请在浏览器完成验证。"
                        )
                    if not article and urlsplit(target).netloc != urlsplit(url).netloc:
                        raise ParseError("RSS 重定向到其他主机，请核对订阅地址。")
                    url = target
                    continue
                if response.status_code in (401, 403, 429):
                    raise VerificationRequired(
                        f"来源拒绝访问（HTTP {response.status_code}）；请检查登录或稍后重试。"
                    )
                if response.status_code != 200:
                    raise AtlasError(f"来源请求失败（HTTP {response.status_code}）。")
                data = bytearray()
                for part in response.iter_bytes():
                    data.extend(part)
                    if len(data) > MAX_BODY:
                        raise ParseError("来源响应过大，已停止读取。")
                return data.decode("utf-8-sig", errors="replace")
        except httpx.HTTPError as exc:
            raise AtlasError("公众号来源网络不可用，下次可重试。") from exc
    raise ParseError("来源重定向次数过多。")


def rss_entries(source):
    if "<!DOCTYPE" in source.upper() or "<!ENTITY" in source.upper():
        raise ParseError("RSS 含不支持的实体声明。")
    try:
        root = ET.fromstring(source)
    except ET.ParseError as exc:
        raise ParseError("来源未返回有效 RSS / Atom，可能需要重新登录。") from exc
    if root.tag not in ("rss", "{http://www.w3.org/2005/Atom}feed"):
        raise ParseError("来源不是 RSS / Atom。")
    entries = []
    for entry in root.iter():
        if entry.tag not in ("item", "{http://www.w3.org/2005/Atom}entry"):
            continue
        links = [
            c.attrib.get("href") or c.text or ""
            for c in entry
            if c.tag.rsplit("}", 1)[-1] in ("link", "guid")
        ]
        for link in links:
            try:
                url = article_url(link)
            except (AtlasError, ValueError):
                continue
            if url not in entries:
                entries.append(url)
            break
        else:
            raise ParseError("RSS 条目缺少原始公众号文章链接；请启用 RSS 的原文链接输出。")
    return entries


def read_json(path, default):
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError) as exc:
        raise AtlasError(f"{path.name} 格式无效，请修复后继续。") from exc


def subscriptions(home):
    rules = read_json(home / "wechat-subscriptions.json", {})
    for name, rule in rules.items():
        validate_subscription(name, rule)
    return rules


def validate_subscription(name, rule):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", name) or not isinstance(rule, dict):
        raise AtlasError("公众号订阅名称或结构无效。")
    required = {"label", "biz", "seed", "feed_url", "keywords", "exclude", "since", "enabled"}
    if set(rule) != required or any(
        not isinstance(rule[k], str) for k in ("label", "biz", "seed", "feed_url", "since")
    ):
        raise AtlasError("公众号订阅字段无效。")
    if not rule["label"] or len(rule["label"]) > 100 or type(rule["enabled"]) is not bool:
        raise AtlasError("公众号名称或启用状态无效。")
    for field in ("keywords", "exclude"):
        values = rule[field]
        if (
            not isinstance(values, list)
            or len(values) > 50
            or not all(isinstance(v, str) and 0 < len(v.strip()) <= 200 for v in values)
        ):
            raise AtlasError("公众号过滤词必须为短文本数组。")
    if not re.fullmatch(r"[A-Za-z0-9+/=]{8,100}", rule["biz"]):
        raise AtlasError("请提供公众号稳定标识 __biz。")
    if rule["seed"]:
        article_url(rule["seed"])
    if rule["feed_url"]:
        p = urlsplit(rule["feed_url"])
        if (
            p.scheme not in ("http", "https")
            or not p.hostname
            or p.username
            or p.password
            or p.fragment
        ):
            raise AtlasError("RSS 地址必须为无账号密码的 HTTP(S) URL。")
    if rule["since"]:
        try:
            datetime.strptime(rule["since"], "%Y-%m-%d")
        except ValueError as exc:
            raise AtlasError("起始日期格式为 YYYY-MM-DD。") from exc


def save_subscription(home, name, values):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", name):
        raise AtlasError("订阅名使用小写字母、数字和连字符，最多 40 位。")
    rules = subscriptions(home)
    rule = (
        {
            "label": name,
            "biz": "",
            "seed": "",
            "feed_url": "",
            "keywords": [],
            "exclude": [],
            "since": "",
            "enabled": True,
        }
        | rules.get(name, {})
        | values
    )
    if isinstance(rule.get("seed"), str) and rule["seed"]:
        rule["seed"] = article_url(rule["seed"])
    validate_subscription(name, rule)
    if name in rules and rules[name]["biz"] != rule["biz"]:
        raise AtlasError("不能替换已登记订阅的公众号身份；请使用新的订阅名。")
    # Re-evaluate earlier exclusions when filters change; stored article IDs
    # still deduplicate and unchanged Wiki hashes remain valid.
    if name in rules and any(rule[k] != rules[name][k] for k in ("keywords", "exclude", "since")):
        path = home / "wechat-state.json"
        state = read_json(path, {})
        if name in state:
            state[name]["done"] = []
            atomic_write(path, json.dumps(state, ensure_ascii=False, indent=2))
    rules[name] = rule
    atomic_write(
        home / "wechat-subscriptions.json", json.dumps(rules, ensure_ascii=False, indent=2)
    )
    return {"name": name, **rule}


def keep_article(store, home, client, url, biz="", collection="wechat:shared", rule=None):
    source = get_text(client, url, article=True)
    item = parse_article(source, url, biz)
    if rule:
        text = item.text.casefold()
        if (rule["keywords"] and not any(k.casefold() in text for k in rule["keywords"])) or any(
            k.casefold() in text for k in rule["exclude"]
        ):
            return {"added": 0, "updated": 0, "filtered": True}
        if rule["since"]:
            if not item.published_at:
                raise ParseError("文章缺少发布日期，无法核对起始日期，保留待处理。")
            if item.published_at[:10] < rule["since"]:
                return {"added": 0, "updated": 0, "filtered": True}
    identity = Identity("wechat", "local-reader")
    store.bind_owner(identity)
    run = store.start("wechat", "wechat-html")
    try:
        a, u = store.commit_page(
            identity,
            "wechat-html",
            run,
            Page([item], None, {"url": article_url(url), "html": source}),
            None,
            None,
            collection,
        )
        store.finish(run, "complete")
    except Exception:
        store.finish(run, "failed", "文章提交失败，下次重试。")
        raise
    return {"added": a, "updated": u, "author": item.author, "item_id": item.item_id}


def client_for_wechat():
    return httpx.Client(
        timeout=30,
        trust_env=False,
        follow_redirects=False,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://mp.weixin.qq.com/"},
    )


def connect_rss(home, base_url):
    p = urlsplit(base_url)
    if (
        p.scheme not in ("http", "https")
        or not p.hostname
        or p.username
        or p.password
        or p.query
        or p.fragment
    ):
        raise AtlasError("WeRSS 地址须为无凭据、无查询参数的 HTTP(S) 基础地址。")
    value = {"base_url": base_url.rstrip("/")}
    atomic_write(home / "wechat-discovery.json", json.dumps(value, indent=2))
    return value


def resolve_feeds(home, client, rules):
    """Match only configured account labels; article __biz is checked at intake."""
    configured = read_json(home / "wechat-discovery.json", {})
    base = configured.get("base_url")
    if not base or not any(r["enabled"] and not r["feed_url"] for r in rules.values()):
        return rules
    catalogue = {}
    for offset in range(0, 300, 30):
        source = get_text(client, base + "/rss?" + urlencode({"limit": 30, "offset": offset}))
        if "<!DOCTYPE" in source.upper() or "<!ENTITY" in source.upper():
            raise ParseError("WeRSS 目录包含不支持的实体声明。")
        try:
            root = ET.fromstring(source)
        except ET.ParseError as exc:
            raise ParseError("WeRSS 目录不可用，请检查服务登录状态。") from exc
        if root.tag != "rss":
            raise ParseError("WeRSS 目录未返回 RSS。")
        entries = root.findall("./channel/item")
        for entry in entries:
            label = (entry.findtext("title") or "").strip()
            url = urljoin(base + "/", entry.findtext("link") or "")
            p = urlsplit(url)
            if p.netloc == urlsplit(base).netloc and re.fullmatch(r"/rss/[^/]+", p.path):
                catalogue.setdefault(label, set()).add(url)
        if len(entries) < 30:
            break
    for key, rule in list(rules.items()):
        candidates = catalogue.get(rule["label"], set())
        if rule["enabled"] and not rule["feed_url"] and len(candidates) == 1:
            save_subscription(home, key, {"feed_url": next(iter(candidates))})
    return subscriptions(home)


def sync_subscriptions(
    store, home, *, name=None, limit=20, max_pages=10, client=None, sleep=time.sleep
):
    rules = subscriptions(home)
    if name and name not in rules:
        raise AtlasError("公众号订阅不存在。")
    state_path = home / "wechat-state.json"
    state = read_json(state_path, {})
    for entry in state.values():
        if not isinstance(entry, dict) or any(
            not isinstance(entry.get(k, []), list)
            or not all(isinstance(u, str) for u in entry.get(k, []))
            for k in ("done", "pending")
        ):
            raise AtlasError("wechat-state.json 的进度记录无效。")
    result = {"added": 0, "updated": 0, "failed": 0, "needs_setup": 0, "subscriptions": {}}
    own = client is None
    client = client or client_for_wechat()
    attempted = 0
    try:
        try:
            rules = resolve_feeds(home, client, rules)
        except AtlasError as exc:
            result.update(failed=1, discovery_error=str(exc))
        result["needs_setup"] = sum(
            1
            for key, rule in rules.items()
            if rule["enabled"] and not rule["feed_url"] and (not name or key == name)
        )
        for key, rule in rules.items():
            if (name and key != name) or not rule.get("enabled", True):
                continue
            progress = state.setdefault(key, {"done": [], "pending": []})
            done, pending = set(progress.get("done", [])), list(progress.get("pending", []))
            report = {"label": rule["label"], "added": 0, "updated": 0, "failed": 0}
            result["subscriptions"][key] = report
            try:
                if rule.get("seed") and rule["seed"] not in done and rule["seed"] not in pending:
                    pending.append(rule["seed"])
                if rule.get("feed_url"):
                    # WeRSS offsets page through its stored feed, never trigger upstream refresh.
                    parsed = urlsplit(rule["feed_url"])
                    paginated = bool(re.fullmatch(r"/rss/[^/]+", parsed.path))
                    previous = None
                    offset = 0
                    for _page in range(max_pages if paginated else 1):
                        params = dict(parse_qs(parsed.query))
                        if paginated:
                            params.update(offset=[str(offset)], limit=["30"])
                        feed_url = urlunsplit(parsed._replace(query=urlencode(params, doseq=True)))
                        entries = rss_entries(get_text(client, feed_url))
                        if not entries or entries == previous:
                            break
                        fresh = [u for u in entries if u not in done and u not in pending]
                        pending.extend(fresh)
                        if entries and all(u in done for u in entries):
                            break
                        previous = entries
                        offset += len(entries)
                    else:
                        if paginated:
                            report["coverage_limited"] = True
                    report["discovery"] = "rss"
                else:
                    report.update(
                        discovery="needs_feed",
                        notice="已登记账号；需要配置并验证 RSS 才能发现后续新文章。",
                    )
                # Persist the discovered queue before reading any article.
                progress.update(pending=pending, last_attempt=now())
                atomic_write(state_path, json.dumps(state, ensure_ascii=False, indent=2))
                for url in list(pending):
                    if attempted >= limit:
                        break
                    if attempted:
                        sleep(2)
                    attempted += 1
                    try:
                        receipt = keep_article(
                            store, home, client, url, rule["biz"], "wechat:" + key, rule
                        )
                    except VerificationRequired:
                        raise
                    except AtlasError as exc:
                        report["failed"] += 1
                        report["error"] = str(exc)
                        continue
                    for field in ("added", "updated"):
                        report[field] += receipt[field]
                    pending.remove(url)
                    done.add(url)
                    progress.update(pending=pending, done=sorted(done))
                    atomic_write(state_path, json.dumps(state, ensure_ascii=False, indent=2))
                if not report["failed"] and rule.get("feed_url"):
                    progress.update(last_success=now())
            except AtlasError as exc:
                report.update(failed=report["failed"] + 1, error=str(exc))
                if isinstance(exc, VerificationRequired):
                    result["verification_required"] = True
            progress.update(pending=pending, done=sorted(done), last_report=report)
            report["pending"] = len(pending)
            atomic_write(state_path, json.dumps(state, ensure_ascii=False, indent=2))
            for field in ("added", "updated", "failed"):
                result[field] += report[field]
            if result.get("verification_required"):
                break
        return result
    finally:
        if own:
            client.close()
