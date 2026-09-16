import json

import pytest

from bookmark_atlas.adapters.x_web import parse_tweet
from bookmark_atlas.models import Identity, Page
from bookmark_atlas.replay import replay
from bookmark_atlas.store import Store

OWNER = Identity("x", "owner-one", "example")


def tweet(identifier, text="AI agent 开源教程", lang="zh", **legacy):
    """A tweet object shaped the way the web adapter expects to read it."""
    return {
        "__typename": "Tweet",
        "rest_id": identifier,
        "legacy": {
            "full_text": text,
            "created_at": "Mon Sep 15 10:00:00 +0000 2026",
            "lang": lang,
            "entities": {},
            **legacy,
        },
        "core": {
            "user_results": {"result": {"rest_id": "u1", "core": {"screen_name": "author-one"}}}
        },
    }


def captured(tweets):
    """A page whose raw payload the real web adapter can parse."""
    entries = [{"content": {"itemContent": {"tweet_results": {"result": t}}}} for t in tweets]
    raw = {"data": {"timeline": {"instructions": [{"entries": entries}]}}}
    return Page([parse_tweet(t) for t in tweets], None, raw)


@pytest.fixture
def archive(tmp_path):
    """A store holding one captured page, as a sync would have left it."""
    store = Store(tmp_path)
    store.bind_owner(OWNER)
    with store.db:
        run_id = store.start("x", "web")
        store.commit_page(
            OWNER,
            "web",
            run_id,
            captured([tweet("a"), tweet("b"), tweet("c", is_quote_status=True)]),
            None,
            None,
        )
        store.finish(run_id, "complete")
    yield store
    store.close()


def forget_fields(store, *fields):
    """Drop fields from stored rows, imitating rows written by an older parser."""
    for row in list(store.db.execute("SELECT item_id, document FROM items")):
        document = json.loads(row["document"])
        for field in fields:
            document.pop(field, None)
        with store.db:
            store.db.execute(
                "UPDATE items SET document=? WHERE item_id=?",
                (json.dumps(document, ensure_ascii=False), row["item_id"]),
            )


def stored_kind(store, item_id):
    document = store.db.execute(
        "SELECT document FROM items WHERE item_id=?", (item_id,)
    ).fetchone()[0]
    return json.loads(document).get("kind")


def test_replay_recovers_fields_the_archive_never_stored(archive):
    """Older rows predate fields the parser now reads; replay fills them in."""
    forget_fields(archive, "kind", "language")

    report = replay(archive)
    assert report["parsed"] == 3
    assert report["changed"] == 3
    assert report["applied"] is False  # read-only unless asked
    assert stored_kind(archive, "c") is None

    applied = replay(archive, apply=True)
    assert applied["applied"] is True
    assert stored_kind(archive, "c") == "quote"

    # Replaying again finds nothing left to do.
    assert replay(archive)["changed"] == 0


def test_replay_leaves_memberships_and_captures_alone(archive):
    """Replay reads raw responses; it must not duplicate them or alter ownership."""

    def counts():
        return tuple(
            archive.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("captures", "memberships", "collection_memberships")
        )

    before = counts()
    replay(archive, apply=True)
    assert counts() == before


def test_replay_reports_unreadable_pages_without_failing(archive):
    """A capture the current parser cannot read is counted, not fatal."""
    with archive.db:
        archive.db.execute(
            "INSERT INTO captures(run_id,captured_at,cursor,payload) VALUES(?,?,?,?)",
            (0, "2026-09-15T00:00:00+00:00", None, json.dumps({"data": {}})),
        )
    report = replay(archive)
    assert report["captures"] == 2
    assert report["parse_failures"] == 1
    assert report["parsed"] == 3
