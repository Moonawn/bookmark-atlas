import json
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from test_core import Fake, execute, item, page
from test_preferences_wiki import payload

from bookmark_atlas.adapters.x_api import XAPI, parse_page
from bookmark_atlas.analysis import LocalAnalysis
from bookmark_atlas.models import AtlasError, Identity, ParseError
from bookmark_atlas.store import Store, content_digest
from bookmark_atlas.sync import sync_one
from bookmark_atlas.watch import (
    AuthorStream,
    Transport,
    roster,
    run_watches,
    save_rule,
    validate_rule,
)
from bookmark_atlas.wiki import apply_notes, export_wiki


@pytest.mark.parametrize(
    "rule",
    [
        {},
        {"authors": ["bad/name"]},
        {"authors": ["good"], "days": 0},
        {"following": True, "max_authors": True},
        {"following": True, "kinds": ["video"]},
        {"following": True, "language": "../x"},
    ],
)
def test_invalid_rules_rejected(rule):
    with pytest.raises(AtlasError):
        validate_rule("valid", rule)


def test_collections_keep_memberships_and_progress_separate(tmp_path):
    s = Store(tmp_path)
    try:
        a = Fake({None: page(item("a"), cursor="older")})
        sync_one(s, a, max_pages=1, delay=0, collection="watch:one:alice")
        assert s.known(Identity("x", "owner-one")) == set()
        assert s.checkpoint("x", "api") is None
        assert s.checkpoint("x", "api:watch:one:alice") == "older"
        assert execute(s, Fake({None: page(item("a"))}))["added"] == 1
        assert (
            sync_one(s, Fake({None: page(item("a"))}), delay=0, collection="watch:two:alice")[
                "added"
            ]
            == 1
        )
        assert len(s.items()) == 1
        assert len(s.items()[0]["collections"]) == 3
        assert s.db.execute("SELECT COUNT(*) FROM memberships").fetchone()[0] == 1
    finally:
        s.close()


def test_migrate_existing_v1_memberships(tmp_path):
    db = sqlite3.connect(tmp_path / "atlas.sqlite3")
    db.executescript(
        "CREATE TABLE memberships(site TEXT, account_id TEXT,item_id TEXT,first_seen TEXT,last_seen TEXT,PRIMARY KEY(site,account_id,item_id)); INSERT INTO memberships VALUES('x','owner','old','t','t'); PRAGMA user_version=1;"
    )
    db.close()
    s = Store(tmp_path)
    assert s.known(Identity("x", "owner")) == {"old"}
    assert s.db.execute("PRAGMA user_version").fetchone()[0] == 2
    s.close()


def test_new_filter_metadata_does_not_invalidate_old_analysis():
    doc = item("a").to_dict()
    old = {k: v for k, v in doc.items() if k not in ("kind", "language")}
    assert content_digest(old) == content_digest(doc)


def test_author_filters_preserve_cursor_across_unmatched_pages():
    recent = datetime.now(UTC)
    start = recent - timedelta(days=7)

    def post(id, text="AI news", **extra):
        return item(
            id, text, author_id="42", published_at=recent.isoformat(), language="en", **extra
        )

    class Feed:
        name = "web"

        def posts(self, *args):
            return page(post("a"), post("b", kind="reply"), post("c", "sports"), cursor="more")

    rule = validate_rule("test", {"authors": ["alice"], "keywords": ["ai"], "language": "en"})
    stream = AuthorStream(Feed(), {"id": "42"}, rule, start)
    result = stream.page()
    assert [p.item_id for p in result.items] == ["a"]
    assert result.next_cursor == "more"
    rule["keywords"] = ["missing"]
    assert stream.page().next_cursor == "more"


def test_pinned_old_post_does_not_end_timeline():
    start = datetime.now(UTC) - timedelta(days=7)

    class Feed:
        name = "web"

        def posts(self, *args):
            return page(
                item("old", published_at=(start - timedelta(days=1)).isoformat()), cursor="more"
            )

    stream = AuthorStream(Feed(), {"id": "42"}, validate_rule("r", {"authors": ["alice"]}), start)
    assert stream.page().next_cursor == "more"


def test_filtered_repeated_cursor_is_error():
    class Feed:
        name = "web"

        def posts(self, *args):
            return page(item("a", published_at=datetime.now(UTC).isoformat()), cursor="repeat")

    stream = AuthorStream(
        Feed(),
        {"id": "42"},
        validate_rule("r", {"authors": ["alice"]}),
        datetime.now(UTC) - timedelta(days=1),
    )
    with pytest.raises(ParseError):
        stream.page("repeat")


def test_api_author_and_following_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_X_ACCESS_TOKEN", "synthetic")
    calls = []

    def handle(req):
        calls.append(req)
        if req.url.path.endswith("/me"):
            return httpx.Response(200, json={"data": {"id": "owner", "username": "owner"}})
        if req.url.path.endswith("/following"):
            return httpx.Response(
                200, json={"data": [{"id": "42", "username": "alice"}], "meta": {}}
            )
        if "/by/username/" in req.url.path:
            return httpx.Response(200, json={"data": {"id": "42", "username": "alice"}})
        return httpx.Response(200, json={"data": [], "meta": {}})

    backend = Transport(XAPI(tmp_path, httpx.Client(transport=httpx.MockTransport(handle))))
    assert backend.following()[0][0]["id"] == "42"
    user = backend.user("alice")
    backend.posts(user, None, datetime.now(UTC))
    assert calls[-1].url.path == "/2/users/42/tweets"
    assert "start_time" in calls[-1].url.params
    backend.close()


def test_following_selection_does_not_include_unfollowed_author():
    class Following:
        def following(self, cursor):
            return [{"id": "1", "username": "ALICE"}, {"id": "2", "username": "bob"}], None

    rule = validate_rule("r", {"following": True, "authors": ["alice", "carol"]})
    assert roster(Following(), rule, sleep=lambda _: None) == [{"id": "1", "username": "ALICE"}]


def test_author_rotation_and_repeat(tmp_path, monkeypatch):
    import bookmark_atlas.watch as w

    class Backend:
        name = "web"

        def identity(self):
            return Identity("x", "owner")

        def close(self):
            pass

    monkeypatch.setattr(
        w, "roster", lambda *_: [{"id": "1", "username": "alice"}, {"id": "2", "username": "bob"}]
    )
    monkeypatch.setattr(
        Transport,
        "posts",
        lambda self, user, *_: page(
            item(user["id"], author_id=user["id"], published_at=datetime.now(UTC).isoformat())
        ),
    )
    save_rule(tmp_path, "r", {"following": True, "max_authors": 1})
    s = Store(tmp_path)
    first = run_watches(s, tmp_path, {"web": Backend}, mode="web", sleep=lambda _: None)
    second = run_watches(s, tmp_path, {"web": Backend}, mode="web", sleep=lambda _: None)
    assert first["rules"][0]["remaining_authors"] == 1
    assert second["rules"][0]["remaining_authors"] == 0
    assert len(s.items()) == 2 and s.known(Identity("x", "owner")) == set()
    s.close()


def test_api_post_kind():
    p = parse_page(
        {
            "data": [
                {
                    "id": "a",
                    "text": "reply",
                    "lang": "zh",
                    "referenced_tweets": [{"type": "replied_to", "id": "b"}],
                }
            ]
        }
    )
    assert p.items[0].kind == "reply" and p.items[0].language == "zh"


def test_wiki_topics_types_and_links(tmp_path):
    s = Store(tmp_path)
    execute(s, Fake({None: page(item("a"))}))
    target = tmp_path / "wiki"
    export_wiki(s, target, LocalAnalysis.name)
    q = json.loads((target / "queue.json").read_text())["items"]
    data = payload(q)
    data["notes"][0].update(topics=["ai-agents", "development"], type="method", related=["second"])
    second = payload(q, "second")["notes"][0]
    second.update(topics=["learning"], type="concept")
    data["notes"].append(second)
    apply_notes(s, target, data)
    export_wiki(s, target, LocalAnalysis.name)
    text = (target / "index.md").read_text()
    assert "AI 与 Agent" in text and "方法" in text and "学习与方法" in text
    assert "(second.md)" in (target / "notes/knowledge-note.md").read_text()
    assert (target / "log.md").is_file()
    refresh = payload(q)
    apply_notes(s, target, refresh)
    meta = json.loads((target / "notes/knowledge-note.meta.json").read_text())
    assert meta == {"topics": ["ai-agents", "development"], "type": "method", "related": ["second"]}
    data["notes"][0]["topics"] = ["invented"]
    with pytest.raises(AtlasError):
        apply_notes(s, target, data)
    s.close()


def test_web_discovery_cannot_change_archive_owner(monkeypatch):
    from bookmark_atlas.adapters import x_web
    from bookmark_atlas.models import OwnerMismatch

    monkeypatch.setattr(x_web, "discover", lambda *_: ("new", {}, Identity("x", "other")))
    base = x_web.XWeb(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _: pytest.fail("unexpected call"))
        ),
        cookies={"auth_token": "synthetic", "ct0": "synthetic"},
        metadata=("old", {"Bookmarks": {}}, Identity("x", "owner")),
    )
    with pytest.raises(OwnerMismatch):
        base._graphql("Following", {})
    base.close()


def test_web_following_keeps_pagination_and_rejects_unknown_shape():
    class Backend:
        name = "web"
        body = {
            "data": {
                "timeline": {
                    "instructions": [
                        {
                            "entries": [
                                {
                                    "content": {
                                        "itemContent": {
                                            "user_results": {
                                                "result": {
                                                    "rest_id": "42",
                                                    "core": {"screen_name": "alice"},
                                                }
                                            }
                                        }
                                    }
                                },
                                {"content": {"cursorType": "Bottom", "value": "next"}},
                            ]
                        }
                    ]
                }
            }
        }

        def identity(self):
            return Identity("x", "owner")

        def _graphql(self, op, variables):
            assert op == "Following" and variables["userId"] == "owner"
            return self.body

    base = Backend()
    transport = Transport(base)
    assert transport.following() == ([{"id": "42", "username": "alice"}], "next")
    base.body = {"data": {"unexpected": []}}
    with pytest.raises(ParseError):
        transport.following()


def test_watch_rate_limit_prevents_transport_switch(tmp_path):
    import time

    from bookmark_atlas.models import RateLimitError

    class Limited:
        name = "api"

        def identity(self):
            raise RateLimitError(time.time() + 60)

        def close(self):
            pass

    save_rule(tmp_path, "r", {"authors": ["alice"]})
    s = Store(tmp_path)
    factories = {"api": Limited, "web": lambda: pytest.fail("must not fall back")}
    for _ in range(2):
        with pytest.raises(RateLimitError):
            run_watches(s, tmp_path, factories)
    assert s.cooldown("x") > time.time()
    s.close()
