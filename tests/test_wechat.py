import json

import httpx
import pytest

from bookmark_atlas import cli
from bookmark_atlas.media import download, targets
from bookmark_atlas.models import AtlasError, ParseError
from bookmark_atlas.replay import replay
from bookmark_atlas.store import Store
from bookmark_atlas.wechat import (
    VerificationRequired,
    article_url,
    connect_rss,
    get_text,
    keep_article,
    parse_article,
    resolve_feeds,
    rss_entries,
    save_subscription,
    subscriptions,
    sync_subscriptions,
)
from bookmark_atlas.wiki import export_wiki

BIZ = "MzTestAccount=="
URL = "https://mp.weixin.qq.com/s/test-article"
FEED = "http://127.0.0.1:8001/rss/test-feed"


def article(mid="123", biz=BIZ):
    return f"""<html><head><meta property="og:title" content="测试文章 &amp; 技术">
    <script>var biz = '' || '{biz}'; var mid = '' || '' || '{mid}';
    var idx = '1'; var sn = 'abc'; var ct = '1789900000';</script></head>
    <body><span id="js_name">示例公众号</span><div id="js_content">
    <p>这一篇文章用于核实本地抓取与内容归档，包含 Agent 的示例。</p>
    <div><p>嵌套段落应该完整保留。</p></div><p>嵌套之后的结尾也必须保留。</p>
    <img data-src="https://mmbiz.qpic.cn/image/640?wx_fmt=png&amp;from=app">
    <a href="https://example.com/reference">原始资料</a><script>untrusted()</script>
    </div></body></html>"""


def feed(*urls):
    from xml.sax.saxutils import escape

    return (
        '<rss version="2.0"><channel>'
        + "".join(f"<item><link>{escape(u)}</link></item>" for u in urls)
        + "</channel></rss>"
    )


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path)
    yield value
    value.close()


def register(home, name="sample", **overrides):
    return save_subscription(
        home, name, {"biz": BIZ, "label": "示例公众号", "seed": "", "feed_url": FEED, **overrides}
    )


def test_article_extracts_nested_body_stable_id_and_localizable_images():
    item = parse_article(article(), URL, BIZ)
    assert item.item_id == BIZ + ":123:1"
    assert item.author == "示例公众号"
    assert "结尾也必须保留" in item.text
    assert "untrusted" not in item.text
    assert item.links == ["https://example.com/reference"]
    assert item.media[0]["media_url_https"].endswith("?wx_fmt=png&from=app")
    assert (
        parse_article(article(), item.url + "&scene=21#wechat_redirect").to_dict() == item.to_dict()
    )


@pytest.mark.parametrize(
    "source",
    [
        "<html>需要登录</html>",
        article().replace("</div></body></html>", ""),
        article().replace(BIZ, "OtherAccount=="),
    ],
)
def test_incomplete_or_wrong_account_is_never_archived(source):
    with pytest.raises(ParseError):
        parse_article(source, URL, BIZ)


def test_captcha_and_redirect_are_not_treated_as_empty_content():
    with pytest.raises(VerificationRequired):
        parse_article("环境异常", URL)
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    302, headers={"location": "https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha"}
                )
            )
        ) as client,
        pytest.raises(VerificationRequired),
    ):
        get_text(client, URL, article=True)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/s",
        "https://mp.weixin.qq.com.evil.test/s/a",
        "https://mp.weixin.qq.com/mp/profile_ext",
        "https://user@mp.weixin.qq.com/s/a",
    ],
)
def test_article_urls_cannot_target_unrelated_hosts(url):
    with pytest.raises(ParseError):
        article_url(url)


def test_feed_parses_rss_and_atom_and_rejects_login_html():
    assert rss_entries(feed(URL)) == [URL]
    assert rss_entries(
        f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><link href="{URL}"/></entry></feed>'
    ) == [URL]
    for source in (
        "<html>login</html>",
        '<!DOCTYPE rss [<!ENTITY x "boom">]><rss/>',
        "<rss><channel><item><link>http://localhost/admin</link></item></channel></rss>",
    ):
        with pytest.raises(ParseError):
            rss_entries(source)


def test_import_deduplicates_alias_and_exports_wiki_and_replays(store, tmp_path):
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=article()))
    ) as client:
        assert keep_article(store, tmp_path, client, URL)["added"] == 1
        long_url = store.items()[0]["document"]["url"]
        assert keep_article(store, tmp_path, client, long_url)["updated"] == 0
    export_wiki(store, tmp_path / "wiki", "local-v1")
    queue = json.loads((tmp_path / "wiki/queue.json").read_text())
    assert len(queue["items"]) == 1
    assert "结尾也必须保留" in queue["items"][0]["text"]
    receipt = replay(store)
    assert receipt["known"] == 1 and receipt["parse_failures"] == 0


def test_failed_article_survives_feed_rollover_and_successes_not_refetched(store, tmp_path):
    register(tmp_path)
    failing = True
    seen = []
    other = URL.replace("test-article", "second-article")

    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "127.0.0.1":
            if request.url.params.get("offset") != "0" or not failing:
                return httpx.Response(200, text=feed())
            return httpx.Response(200, text=feed(URL, other))
        if str(request.url) == other and failing:
            return httpx.Response(500)
        return httpx.Response(200, text=article("456" if str(request.url) == other else "123"))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = sync_subscriptions(store, tmp_path, client=client, sleep=lambda n: None)
        assert first["added"] == 1 and first["failed"] == 1
        failing = False
        seen.clear()
        second = sync_subscriptions(store, tmp_path, client=client, sleep=lambda n: None)
        assert second["added"] == 1 and not second["failed"]
        assert URL not in seen and other in seen
        assert sync_subscriptions(store, tmp_path, client=client)["added"] == 0


def test_captcha_stops_other_accounts_and_preserves_pending(store, tmp_path):
    register(tmp_path)
    register(tmp_path, "second")
    seen = []

    def handler(request):
        seen.append(request.url.host)
        if request.url.host == "127.0.0.1":
            return httpx.Response(
                200, text=feed(URL) if request.url.params["offset"] == "0" else feed()
            )
        return httpx.Response(200, text="环境异常")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        receipt = sync_subscriptions(store, tmp_path, client=client)
    assert receipt["verification_required"]
    assert len(receipt["subscriptions"]) == 1
    assert seen.count("mp.weixin.qq.com") == 1
    assert json.loads((tmp_path / "wechat-state.json").read_text())["sample"]["pending"] == [URL]
    assert not store.items()


def test_filters_reconfiguration_and_identity_guard(store, tmp_path):
    register(tmp_path, keywords=["unrelated"])
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=article()))
    ) as client:
        result = keep_article(
            store, tmp_path, client, URL, BIZ, rule=subscriptions(tmp_path)["sample"]
        )
    assert result["filtered"] and not store.items()
    (tmp_path / "wechat-state.json").write_text(
        json.dumps({"sample": {"done": [URL], "pending": []}})
    )
    save_subscription(tmp_path, "sample", {"keywords": ["Agent"]})
    assert json.loads((tmp_path / "wechat-state.json").read_text())["sample"]["done"] == []
    with pytest.raises(AtlasError):
        save_subscription(tmp_path, "sample", {"biz": "OtherAccount=="})


def test_media_does_not_add_x_parameters_and_sends_referer(tmp_path):
    item = parse_article(article(), URL)
    target = targets(item.to_dict())[0]
    assert target["url"] == item.media[0]["media_url_https"]

    def handler(request):
        assert request.headers["referer"] == "https://mp.weixin.qq.com/"
        return httpx.Response(200, content=b"png", headers={"content-type": "image/png"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert download(client, target["url"], tmp_path / "image.part", 100) == (3, "image/png")


def test_wechat_only_cycle_does_not_require_x_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "factories_for", lambda *a: pytest.fail("X must not be opened"))
    args = cli.parser().parse_args(
        ["--home", str(tmp_path), "sync", "--site", "wechat", "--no-analyze"]
    )
    assert cli.run(args)["wechat"]["failed"] == 0


def test_x_failure_does_not_block_wechat_or_wiki(tmp_path, monkeypatch):
    register(tmp_path)

    def fail(*a, **kw):
        raise AtlasError("X unavailable")

    monkeypatch.setattr(cli, "sync", fail)
    monkeypatch.setattr(cli, "run_watches", lambda *a, **kw: {})
    monkeypatch.setattr(cli, "sync_subscriptions", lambda *a, **kw: {"added": 1, "failed": 0})
    result = cli.run(
        cli.parser().parse_args(["--home", str(tmp_path), "sync", "--organization", "wiki"])
    )
    assert result["failed"] and result["wechat"]["added"] == 1
    assert (tmp_path / "wiki/queue.json").exists()


def test_response_size_bound():
    import bookmark_atlas.wechat as wechat

    # Limits are tested with an isolated mock; never contact a production source.
    old = wechat.MAX_BODY
    try:
        wechat.MAX_BODY = 10
        with (
            httpx.Client(
                transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 11))
            ) as client,
            pytest.raises(ParseError),
        ):
            get_text(client, URL, article=True)
    finally:
        wechat.MAX_BODY = old


def test_existing_werss_catalogue_resolves_only_named_accounts(tmp_path):
    register(tmp_path, feed_url="")
    connect_rss(tmp_path, "http://127.0.0.1:8001")
    catalogue = "<rss><channel><item><title>示例公众号</title><link>http://127.0.0.1:8001/rss/123</link></item><item><title>其他账号</title><link>http://127.0.0.1:8001/rss/456</link></item></channel></rss>"
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=catalogue))
    ) as client:
        rules = resolve_feeds(tmp_path, client, subscriptions(tmp_path))
    assert set(rules) == {"sample"}
    assert rules["sample"]["feed_url"].endswith("/rss/123")


def test_empty_catalogue_is_setup_required_not_active(store, tmp_path):
    register(tmp_path, feed_url="")
    connect_rss(tmp_path, "http://127.0.0.1:8001")
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=feed()))
    ) as client:
        result = sync_subscriptions(store, tmp_path, client=client)
    assert result["needs_setup"] == 1
    assert result["subscriptions"]["sample"]["discovery"] == "needs_feed"
    assert "last_success" not in json.loads((tmp_path / "wechat-state.json").read_text())["sample"]


def test_server_page_size_clamp_does_not_skip_entries(store, tmp_path):
    register(tmp_path)
    offsets = []

    def handler(request):
        if request.url.host == "127.0.0.1":
            offset = int(request.url.params["offset"])
            offsets.append(offset)
            return httpx.Response(
                200,
                text=feed(f"https://mp.weixin.qq.com/s/entry-{offset}") if offset < 3 else feed(),
            )
        return httpx.Response(200, text=article(request.url.path.rsplit("-", 1)[-1]))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = sync_subscriptions(store, tmp_path, client=client, sleep=lambda n: None)
    assert offsets == [0, 1, 2, 3]
    assert result["added"] == 3


def test_verified_shorter_article_edit_updates_hash(store, tmp_path):
    source = article()
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=source))
    ) as client:
        keep_article(store, tmp_path, client, URL)
        before = store.items()[0]["content_hash"]
        source = source.replace("<p>嵌套之后的结尾也必须保留。</p>", "")
        assert keep_article(store, tmp_path, client, URL)["updated"] == 1
    assert store.items()[0]["content_hash"] != before
    assert "结尾也必须保留" not in store.items()[0]["document"]["text"]
