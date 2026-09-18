import base64
import hashlib
import json

import httpx
import pytest

from bookmark_atlas.adapters.x_api import XAPI
from bookmark_atlas.adapters.x_api import parse_page as parse_api
from bookmark_atlas.adapters.x_web import XWeb, identity_from_html, metadata_from_script
from bookmark_atlas.adapters.x_web import parse_page as parse_web
from bookmark_atlas.analysis import OllamaAnalysis
from bookmark_atlas.config import read_credentials, save_credentials
from bookmark_atlas.http import json_body, request
from bookmark_atlas.models import (
    AtlasError,
    AuthError,
    Identity,
    ParseError,
    RateLimitError,
    UnavailableError,
)
from bookmark_atlas.oauth import pkce


def test_api_pagination_identity_expansions_and_long_text(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_X_ACCESS_TOKEN", "synthetic-token")
    calls = []

    def handler(req):
        calls.append(req)
        assert req.headers["Authorization"] == "Bearer synthetic-token"
        if req.url.path == "/2/users/me":
            return httpx.Response(200, json={"data": {"id": "10", "username": "example"}})
        assert req.url.path == "/2/users/10/bookmarks"
        assert req.url.params["pagination_token"] == "next-page"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "1",
                        "text": "preview",
                        "author_id": "2",
                        "note_tweet": {"text": "complete long post"},
                        "attachments": {"media_keys": ["m1"]},
                    }
                ],
                "includes": {
                    "users": [{"id": "2", "username": "writer"}],
                    "media": [{"media_key": "m1", "url": "https://pbs.twimg.com/example.jpg"}],
                },
                "meta": {"next_token": "older"},
            },
        )

    adapter = XAPI(tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert adapter.identity().account_id == "10"
    result = adapter.page("next-page")
    assert result.next_cursor == "older"
    assert result.items[0].text == "complete long post"
    assert result.items[0].author == "writer"
    assert len(result.items[0].media) == 1
    assert len(calls) == 2
    adapter.close()


@pytest.mark.parametrize(
    "body", [{}, {"errors": [{"detail": "private"}]}, {"data": [{"text": "missing id"}]}]
)
def test_api_malformed_or_partial_not_silent_empty(body):
    with pytest.raises(ParseError):
        parse_api(body)


def test_api_empty_collection():
    assert parse_api({"meta": {"result_count": 0}}).items == []


def web_body(result):
    return {
        "data": {
            "bookmark_timeline_v2": {
                "timeline": {
                    "instructions": [
                        {
                            "type": "TimelineAddEntries",
                            "entries": [
                                {"content": {"itemContent": {"tweet_results": {"result": result}}}},
                                {"content": {"cursorType": "Bottom", "value": "older"}},
                            ],
                        }
                    ]
                }
            }
        }
    }


def test_web_nested_visibility_long_post_and_quote_not_membership():
    result = {
        "__typename": "TweetWithVisibilityResults",
        "tweet": {
            "rest_id": "11",
            "legacy": {
                "full_text": "short &amp; text",
                "created_at": "Mon Sep 07 01:00:00 +0000 2026",
            },
            "core": {
                "user_results": {"result": {"rest_id": "2", "core": {"screen_name": "writer"}}}
            },
            "note_tweet": {"note_tweet_results": {"result": {"text": "完整长文"}}},
            "quoted_status_result": {
                "result": {
                    "rest_id": "12",
                    "legacy": {
                        "full_text": "quoted",
                        "extended_entities": {
                            "media": [
                                {
                                    "media_key": "3_999",
                                    "type": "photo",
                                    "media_url_https": "https://pbs.twimg.com/media/q.jpg",
                                }
                            ]
                        },
                    },
                    "core": {
                        "user_results": {
                            "result": {"rest_id": "3", "core": {"screen_name": "quoted_writer"}}
                        }
                    },
                }
            },
        },
    }
    page = parse_web(web_body(result))
    assert len(page.items) == 1
    # The quoted post is archived with the bookmark that quotes it, marked so
    # its words and pictures are never mistaken for the bookmarker's own.
    assert page.items[0].text == "完整长文\n\n【引用 @quoted_writer】\nquoted"
    assert page.items[0].author == "writer"
    assert [m["media_url_https"] for m in page.items[0].media] == [
        "https://pbs.twimg.com/media/q.jpg"
    ]
    assert page.items[0].media[0]["quoted"] is True
    assert page.items[0].published_at == "2026-09-07T01:00:00+00:00"
    assert page.next_cursor == "older"


def test_web_article_body_and_images_replace_a_bare_link():
    """An article post carries only a t.co link; the article is the content.

    Without this the archive keeps "https://t.co/xxxx" as the whole post —
    which is what it did for 53 bookmarks before the parser read the field.
    """
    result = {
        "rest_id": "21",
        "legacy": {
            "full_text": "https://t.co/abc123",
            "created_at": "Mon Sep 07 01:00:00 +0000 2026",
        },
        "core": {"user_results": {"result": {"rest_id": "2", "core": {"screen_name": "writer"}}}},
        "article": {
            "article_results": {
                "result": {
                    "title": "标题",
                    "plain_text": "正文第一段\n\n正文第二段",
                    "media_entities": [
                        {"media_info": {"original_img_url": "https://pbs.twimg.com/media/one.jpg"}},
                        {"media_info": {}},  # no image URL: must not become a media entry
                    ],
                }
            }
        },
    }
    page = parse_web(web_body(result))
    assert len(page.items) == 1
    item = page.items[0]
    assert item.text == "标题\n\n正文第一段\n\n正文第二段"
    assert "t.co" not in item.text
    assert item.media == [
        {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/one.jpg"}
    ]


def test_web_tombstone_preserved_in_raw_not_a_fake_item():
    body = web_body({"__typename": "TweetTombstone"})
    page = parse_web(body)
    assert page.items == [] and page.raw == body


@pytest.mark.parametrize("body", [{}, {"data": {}}, web_body({"rest_id": "1"})])
def test_web_schema_changes_fail_closed(body):
    with pytest.raises(ParseError):
        parse_web(body)


def test_web_rate_and_auth_errors():
    with pytest.raises(RateLimitError):
        parse_web({"errors": [{"code": 88}]})
    with pytest.raises(AuthError):
        parse_web({"errors": [{"code": 89}]})


def test_metadata_discovery_from_synthetic_script():
    bearer, operations = metadata_from_script(
        'const t="AAAAA'
        + "B" * 45
        + '";let a={queryId:"abc123",operationName:"Bookmarks",operationType:"query",metadata:{featureSwitches:["feature_one","feature_two"]}}'
    )
    assert bearer.startswith("AAAAA")
    assert operations["Bookmarks"] == {
        "query_id": "abc123",
        "features": ["feature_one", "feature_two"],
    }


def test_web_identity_and_pagination_with_authenticated_mock():
    calls = []

    def handler(req):
        calls.append(req)
        assert req.url.host == "x.com"
        assert "auth_token=synthetic" in req.headers["cookie"]
        assert json.loads(req.url.params["variables"])["cursor"] == "next"
        return httpx.Response(200, json=web_body({"rest_id": "1", "legacy": {"full_text": "test"}}))

    adapter = XWeb(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cookies={"auth_token": "synthetic", "ct0": "synthetic", "twid": "u%3D10"},
        metadata=(
            "synthetic",
            {"Bookmarks": {"query_id": "test", "features": []}},
            Identity("x", "10", "example"),
        ),
    )
    assert adapter.identity().account_id == "10"
    assert adapter.page("next").items[0].item_id == "1"
    assert len(calls) == 1
    adapter.close()


def test_rate_limit_no_retry_or_body_leak():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(429, headers={"retry-after": "600"}, text="SENSITIVE BODY")

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(RateLimitError) as caught,
    ):
        request(client, "GET", "https://api.x.com/2/users/me")
    assert len(calls) == 1
    assert "SENSITIVE" not in str(caught.value)


@pytest.mark.parametrize(
    "status,exception",
    [(401, AuthError), (403, AuthError), (402, UnavailableError), (302, UnavailableError)],
)
def test_http_error_types_and_redaction(status, exception):
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(status, text="SECRET"))
        ) as client,
        pytest.raises(exception) as caught,
    ):
        request(client, "GET", "https://api.x.com/2/users/me")
    assert "SECRET" not in str(caught.value)


def test_transient_server_errors_retry_with_backoff():
    statuses = iter([503, 502, 200])
    waits = []
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(next(statuses), json={}))
    ) as client:
        assert request(client, "GET", "https://api.x.com", sleep=waits.append).status_code == 200
    assert waits == [1, 2]


def test_non_json_rejected():
    with pytest.raises(ParseError):
        json_body(httpx.Response(200, text="<html>login</html>"))


def test_pkce_s256():
    verifier, challenge = pkce()
    assert len(verifier) >= 43
    assert challenge == base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")


def test_refresh_token_saved_privately(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLAS_X_ACCESS_TOKEN", raising=False)
    save_credentials(
        tmp_path,
        {
            "client_id": "example",
            "access_token": "expired",
            "refresh_token": "refresh",
            "expires_at": 1,
        },
    )

    def handler(req):
        assert req.url.path == "/2/oauth2/token"
        assert "refresh_token=refresh" in req.content.decode()
        return httpx.Response(
            200, json={"access_token": "new", "refresh_token": "rotated", "expires_in": 7200}
        )

    adapter = XAPI(tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert adapter.token == "new"
    assert read_credentials(tmp_path)["refresh_token"] == "rotated"
    assert (tmp_path / "credentials.json").stat().st_mode & 0o777 == 0o600
    adapter.close()


def test_ollama_local_only_and_validated_output():
    with pytest.raises(AtlasError):
        OllamaAnalysis("model", "https://external.example")
    response = {
        "summary": "中文摘要",
        "topics": ["AI"],
        "key_points": ["要点"],
        "action": "阅读原文",
    }
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"message": {"content": json.dumps(response)}})
        )
    ) as client:
        engine = OllamaAnalysis("example-model", client=client)
        result = engine.analyze(
            {"text": "Untrusted source", "links": [], "url": "https://x.com/i/status/1"}
        )
        assert result["summary"] == "中文摘要"
        assert result["source_url"] == "https://x.com/i/status/1"


def test_ollama_bad_schema_can_retry():
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"message": {"content": "{}"}})
            )
        ) as client,
        pytest.raises(AtlasError),
    ):
        OllamaAnalysis("example", client=client).analyze(
            {"text": "x", "links": [], "url": "https://x.com/i/status/1"}
        )


def test_identity_is_read_from_authenticated_server_bootstrap():
    state = {
        "session": {"user_id": "123"},
        "entities": {"users": {"entities": {"123": {"screen_name": "example"}}}},
    }
    assert (
        identity_from_html("window.__INITIAL_STATE__ = " + json.dumps(state) + ";").account_id
        == "123"
    )
    assert (
        identity_from_html("window.__INITIAL_STATE__ = " + json.dumps(state) + ";").username
        == "example"
    )


@pytest.mark.parametrize(
    "source",
    [
        "<html>Login</html>",
        'window.__INITIAL_STATE__={"session":{}};',
        'window.__INITIAL_STATE__={"session":{"user_id":null}};',
    ],
)
def test_missing_server_identity_is_not_an_empty_archive(source):
    with pytest.raises(AuthError):
        identity_from_html(source)


def test_lazy_bookmark_chunk_discovery_and_asset_cookie_isolation(monkeypatch):
    import bookmark_atlas.adapters.x_web as module

    state = {"session": {"user_id": "123"}}
    bootstrap = (
        "window.__INITIAL_STATE__="
        + json.dumps(state)
        + ';<script src="https://abs.twimg.com/main.js"></script>;'
        + '7:"bundle.Bookmarks";7:"0123456789abcdef";'
    )
    main = 'const token="AAAAA' + "B" * 45 + '";'
    chunk = '{queryId:"q",operationName:"Bookmarks",metadata:{featureSwitches:[]}}'
    paths = []

    def assets(req):
        assert "cookie" not in req.headers and "authorization" not in req.headers
        paths.append(req.url.path)
        return httpx.Response(200, text=main if req.url.path == "/main.js" else chunk)

    monkeypatch.setattr(
        module, "network_client", lambda: httpx.Client(transport=httpx.MockTransport(assets))
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=bootstrap))
    ) as client:
        client.cookies.set("auth_token", "synthetic", domain="x.com")
        token, operations, identity = module.discover(client)
    assert identity.account_id == "123"
    assert operations["Bookmarks"]["query_id"] == "q"
    assert any("/bundle.Bookmarks.0123456789abcdefa.js" in p for p in paths)


def test_export_write_does_not_change_existing_parent_permissions(tmp_path):
    from bookmark_atlas.config import atomic_write

    folder = tmp_path / "existing-documents"
    folder.mkdir(mode=0o755)
    atomic_write(folder / "report.md", "private report")
    assert folder.stat().st_mode & 0o777 == 0o755
    assert (folder / "report.md").stat().st_mode & 0o777 == 0o600
