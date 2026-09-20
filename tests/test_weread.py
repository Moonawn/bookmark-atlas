import base64
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_wechat import article, store  # noqa: F401

from bookmark_atlas.models import AtlasError, ParseError
from bookmark_atlas.wechat import (
    VerificationRequired,
    article_url,
    connect_weread,
    save_subscription,
    sync_subscriptions,
)
from bookmark_atlas.weread import REMOTE, WeReadBridge, book_id

BIZ = base64.b64encode(b"1234567890").decode()
URL = "https://mp.weixin.qq.com/s/a~b_c"


def test_bridge_protocol_keeps_credentials_in_container(monkeypatch):
    calls = []

    def run(command, **kwargs):
        request = json.loads(kwargs["input"])
        calls.append(request)
        assert command[:4] == ["docker", "exec", "-i", "we-mp-rss"]
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["timeout"] == 75
        result = (
            {"review_id": "MP_WXS_1234567890_a~b_c"}
            if request["op"] == "latest"
            else {"html": article(biz=BIZ)}
        )
        kwargs["stdout"].write(json.dumps(result).encode())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    bridge = WeReadBridge("we-mp-rss")
    assert bridge.latest(BIZ) == URL
    assert bridge.content(BIZ, URL) == article(biz=BIZ)
    assert calls[-1]["review_id"] == "MP_WXS_1234567890_a~b_c"
    assert article_url(URL) == URL


@pytest.mark.parametrize(
    "payload,exception",
    [
        ({"error": "authorization"}, VerificationRequired),
        ({"error": "upstream"}, AtlasError),
        ({"review_id": "MP_WXS_999_wrong"}, ParseError),
        ({"review_id": "MP_WXS_1234567890_/../../"}, ParseError),
        ([], ParseError),
    ],
)
def test_bridge_rejects_error_and_wrong_account(monkeypatch, payload, exception):
    def run(command, **kwargs):
        kwargs["stdout"].write(json.dumps(payload).encode())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(exception):
        WeReadBridge("we-mp-rss").latest(BIZ)


def test_bridge_rejects_shell_input_and_bad_biz(tmp_path):
    with pytest.raises(AtlasError):
        connect_weread(tmp_path, "container; touch /tmp/test")
    with pytest.raises(ParseError):
        book_id("invalid-biz")
    with pytest.raises(ParseError):
        WeReadBridge("test").content(BIZ, "https://mp.weixin.qq.com/s?__biz=x&mid=1&idx=1")


def test_pending_weread_article_survives_latest_rollover(store, tmp_path):  # noqa: F811
    save_subscription(tmp_path, "sample", {"biz": BIZ, "label": "Example"})
    calls = []

    class Bridge:
        latest_url = URL
        fail = True

        def latest(self, biz):
            return self.latest_url

        def content(self, biz, url):
            calls.append(url)
            if self.fail:
                raise AtlasError("temporary failure")
            return article("123" if url == URL else "456", biz=BIZ)

    bridge = Bridge()
    result = sync_subscriptions(store, tmp_path, bridge=bridge, sleep=lambda _: None)
    assert result["failed"] == 1 and result["needs_setup"] == 0
    bridge.fail = False
    bridge.latest_url = URL + "new"
    result = sync_subscriptions(store, tmp_path, bridge=bridge, sleep=lambda _: None)
    assert result["added"] == 2 and result["failed"] == 0
    assert result["subscriptions"]["sample"]["coverage"] == "latest-only"
    calls.clear()
    again = sync_subscriptions(store, tmp_path, bridge=bridge, sleep=lambda _: None)
    assert again["added"] == again["updated"] == 0 and calls == []


def test_expired_weread_stops_remaining_accounts(store, tmp_path):  # noqa: F811
    for name in ("first", "second"):
        save_subscription(tmp_path, name, {"biz": BIZ, "label": name})

    class Bridge:
        def latest(self, biz):
            raise VerificationRequired("expired")

    result = sync_subscriptions(store, tmp_path, bridge=Bridge(), sleep=lambda _: None)
    assert result["verification_required"] and result["failed"] == 1
    assert len(result["subscriptions"]) == 1


@pytest.mark.parametrize(
    "op,payload,expected",
    [
        (
            "latest",
            '{"reviewId":"MP_WXS_1234567890_a~b_c"}',
            {"review_id": "MP_WXS_1234567890_a~b_c"},
        ),
        ("latest", '{"errcode":-2012}', {"error": "authorization"}),
        ("content", '{"errCode":-2012}', {"error": "authorization"}),
        (
            "content",
            '<div id="js_content">public body</div>',
            {"html": '<div id="js_content">public body</div>'},
        ),
    ],
)
def test_remote_worker_outputs_only_public_payload(monkeypatch, op, payload, expected):
    secret = "private-cookie-do-not-export"
    monkeypatch.setattr(Path, "read_text", lambda *a, **kw: "saved configuration")
    monkeypatch.setitem(
        sys.modules,
        "yaml",
        SimpleNamespace(safe_load=lambda text: {"weread_data": {"cookie": secret}}),
    )

    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, size):
            yield payload.encode()

    class Session:
        headers = {}

        def get(self, url, **kwargs):
            assert self.headers["Cookie"] == secret
            assert url.startswith("https://weread.qq.com/")
            assert not kwargs["allow_redirects"] and kwargs["stream"]
            return Response()

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(Session=Session))
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {"op": op, "book_id": "MP_WXS_1234567890", "review_id": "MP_WXS_1234567890_a~b_c"}
            )
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    exec(compile(REMOTE, "<bridge-worker>", "exec"), {})
    assert secret not in output.getvalue()
    assert json.loads(output.getvalue()) == expected
