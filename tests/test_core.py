import json
import time

import pytest

from bookmark_atlas.analysis import LocalAnalysis, analyze_pending
from bookmark_atlas.export import export_json, export_markdown
from bookmark_atlas.models import (
    AuthError,
    Identity,
    Item,
    OwnerMismatch,
    Page,
    ParseError,
    RateLimitError,
    source_key,
)
from bookmark_atlas.store import Store
from bookmark_atlas.sync import process_lock, sync, sync_one


def item(identifier, text="AI agent 开源教程", **kwargs):
    return Item("x", identifier, text, f"https://x.com/i/status/{identifier}", **kwargs)


def page(*items, cursor=None):
    return Page(list(items), cursor, {"fixture": "synthetic", "ids": [i.item_id for i in items]})


class Fake:
    name = "api"

    def __init__(self, pages, account="owner-one", name="api"):
        self.pages, self.account, self.name = pages, account, name
        self.calls = []
        self.closed = False

    def identity(self):
        return Identity("x", self.account, "example")

    def page(self, cursor=None):
        self.calls.append(cursor)
        result = self.pages[cursor]
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        self.closed = True


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path)
    yield value
    value.close()


def execute(store, fake, **kwargs):
    return sync_one(store, fake, delay=0, **kwargs)


def test_new_old_dated_bookmark_is_new_and_repeat_is_idempotent(store):
    first = Fake({None: page(item("2", published_at="2025-01-01"))})
    assert execute(store, first)["added"] == 1
    before = store.items()[0]["first_seen"]
    assert execute(store, first)["added"] == 0
    second = Fake(
        {None: page(item("1", published_at="2010-01-01"), item("2", published_at="2025-01-01"))}
    )
    assert execute(store, second)["added"] == 1
    assert len(store.items()) == 2
    assert next(r for r in store.items() if r["item_id"] == "2")["first_seen"] == before


def test_two_sources_merge_without_truncation_and_no_false_deletion(store):
    execute(
        store,
        Fake({None: page(item("1", "A very long complete post", author="example"))}, name="web"),
    )
    execute(store, Fake({None: page(item("1", "A short", links=["https://example.com"]))}))
    result = store.items()[0]["document"]
    assert result["text"] == "A very long complete post"
    assert result["author"] == "example"
    assert result["links"] == ["https://example.com"]
    assert store.db.execute("SELECT COUNT(*) FROM origins").fetchone()[0] == 2
    execute(store, Fake({None: page()}))
    assert store.stats()["items"] == 1


def test_changed_text_reanalyzed_unchanged_skipped(store):
    execute(store, Fake({None: page(item("1", "one"))}))
    engine = LocalAnalysis()
    assert analyze_pending(store, engine)["analyzed"] == 1
    assert analyze_pending(store, engine)["analyzed"] == 0
    execute(store, Fake({None: page(item("1", "two"))}))
    assert analyze_pending(store, engine)["analyzed"] == 1


def test_mixed_duplicate_page_does_not_stop_early(store):
    execute(store, Fake({None: page(item("old"))}))
    fake = Fake(
        {None: page(item("old"), item("new"), cursor="next"), "next": page(item("older-new"))}
    )
    assert execute(store, fake)["added"] == 2
    assert fake.calls == [None, "next"]


def test_two_known_pages_stop_incremental(store):
    execute(store, Fake({None: page(item("a"), item("b"), item("c"))}))
    fake = Fake({None: page(item("a"), cursor="b"), "b": page(item("b"), cursor="c")})
    assert execute(store, fake)["status"] == "incremental"
    assert fake.calls == [None, "b"]


def test_resume_after_failure_preserves_raw_and_progress(store):
    with pytest.raises(ParseError):
        execute(store, Fake({None: page(item("a"), cursor="older"), "older": ParseError("broken")}))
    assert store.checkpoint("x", "api") == "older"
    assert store.stats()["items"] == 1
    assert store.db.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 1
    fake = Fake({None: page(item("a"), cursor="irrelevant"), "older": page(item("b"))})
    result = execute(store, fake, overlap_pages=1)
    assert fake.calls == [None, "older"]
    assert result["added"] == 1
    assert store.checkpoint("x", "api") is None


def test_max_pages_checkpoint_is_partial(store):
    result = execute(store, Fake({None: page(item("a"), cursor="older")}), max_pages=1)
    assert result["status"] == "partial"
    assert store.checkpoint("x", "api") == "older"


def test_full_scan_ignores_known_overlap(store):
    execute(store, Fake({None: page(item("a"))}))
    fake = Fake({None: page(item("a"), cursor="next"), "next": page(item("b"))})
    assert execute(store, fake, full=True, overlap_pages=1)["added"] == 1


def test_owner_mismatch_blocks_before_data_write(store):
    execute(store, Fake({None: page(item("a"))}))
    other = Fake({None: page(item("b"))}, account="different")
    with pytest.raises(OwnerMismatch):
        execute(store, other)
    assert other.calls == []
    assert store.stats()["items"] == 1


def test_page_and_checkpoint_rollback_together(store):
    who = Identity("x", "owner-one")
    store.bind_owner(who)
    run = store.start("x", "api")
    bad = Item("other", "b", "text", "https://example.com")
    with pytest.raises(ValueError):
        store.commit_page(who, "api", run, page(item("a"), bad), None, "next")
    assert store.stats()["items"] == 0
    assert store.checkpoint("x", "api") is None
    assert store.db.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 0


def test_cursor_loop_fails_without_losing_prior_pages(store):
    fake = Fake({None: page(item("a"), cursor="loop"), "loop": page(item("b"), cursor="loop")})
    with pytest.raises(ParseError):
        execute(store, fake)
    assert store.stats()["items"] == 1
    assert store.checkpoint("x", "api") == "loop"


def test_auto_fallback_missing_auth(store):
    def unavailable():
        raise AuthError("not configured")

    fake = Fake({None: page(item("1"))}, name="web")
    result = sync(store, {"api": unavailable, "web": lambda: fake}, delay=0)
    assert result["adapter"] == "web"
    assert result["fallbacks"] == [{"adapter": "api", "reason": "not configured"}]
    assert fake.closed


def test_no_fallback_on_rate_limit_and_cooldown_persists(store):
    fake = Fake({None: RateLimitError(time.time() + 600)})
    used = []
    with pytest.raises(RateLimitError):
        sync(store, {"api": lambda: fake, "web": lambda: used.append(True)}, delay=0)
    assert used == [] and fake.closed
    assert store.cooldown("x") > time.time()
    with pytest.raises(RateLimitError):
        sync(store, {"web": lambda: used.append(True)}, mode="web")
    assert used == []


def test_owner_mismatch_never_falls_back(store):
    execute(store, Fake({None: page(item("a"))}))
    other = Fake({}, account="different")
    used = []
    with pytest.raises(OwnerMismatch):
        sync(store, {"api": lambda: other, "web": lambda: used.append(True)})
    assert used == [] and other.closed


def test_process_lock_rejects_concurrent_work(tmp_path):
    with process_lock(tmp_path), pytest.raises(Exception, match="已有同步"), process_lock(tmp_path):
        pass
    with process_lock(tmp_path):
        pass


def test_export_unicode_and_untrusted_content(store, tmp_path):
    execute(
        store,
        Fake(
            {
                None: page(
                    item(
                        "../escape",
                        "<script>alert(1)</script> ![x](https://evil.test) 人工智能教程",
                    )
                )
            }
        ),
    )
    analyze_pending(store, LocalAnalysis())
    export_markdown(store, tmp_path / "report", LocalAnalysis.name)
    files = list((tmp_path / "report/items").glob("*.md"))
    assert len(files) == 1
    content = files[0].read_text()
    assert "<script>" not in content and "![x]" not in content
    assert "原文摘录" in content
    assert "AI 与 Agent" in (tmp_path / "report/index.md").read_text()
    export_json(store, tmp_path / "export.json")
    assert (
        json.loads((tmp_path / "export.json").read_text())["items"][0]["document"]["item_id"]
        == "../escape"
    )


def test_export_points_at_local_media_once_it_is_fetched(store, tmp_path):
    """A downloaded file replaces the remote URL in the report it belongs to."""
    execute(store, Fake({None: page(item("a"))}))
    analyze_pending(store, LocalAnalysis())
    export_markdown(store, tmp_path / "report", LocalAnalysis.name)
    report = next((tmp_path / "report/items").glob("*.md"))
    assert "## 本地媒体" not in report.read_text()  # nothing fetched yet

    key = source_key(next(iter(store.items()))["document"])
    media = tmp_path / "media"
    media.mkdir()
    (media / f"{key}-1.jpg").write_bytes(b"photo")
    (media / f"{key}-2-thumb.jpg").write_bytes(b"frame")
    (media / "index.json").write_text(
        json.dumps(
            {
                key: {
                    "files": [
                        {"n": 1, "kind": "photo", "file": f"{key}-1.jpg"},
                        {
                            "n": 2,
                            "kind": "video",
                            "skipped": "video_not_downloaded",
                            "original": "https://video.twimg.com/x.mp4",
                            "thumbnail": f"{key}-2-thumb.jpg",
                        },
                    ]
                }
            }
        )
    )

    export_markdown(store, tmp_path / "report", LocalAnalysis.name)
    content = next((tmp_path / "report/items").glob("*.md")).read_text()
    assert f"![photo](../../media/{key}-1.jpg)" in content
    assert f"![视频截图](../../media/{key}-2-thumb.jpg)" in content
    # The full video stays discoverable even though only a frame was kept.
    assert "完整视频未下载" in content and "https://video.twimg.com/x.mp4" in content


def test_export_survives_a_damaged_media_index(store, tmp_path):
    """A broken index must not take the reports down with it."""
    execute(store, Fake({None: page(item("a"))}))
    analyze_pending(store, LocalAnalysis())
    media = tmp_path / "media"
    media.mkdir()
    (media / "index.json").write_text("{ not json")
    export_markdown(store, tmp_path / "report", LocalAnalysis.name)
    assert next((tmp_path / "report/items").glob("*.md")).is_file()


def test_analysis_failure_does_not_lose_items_and_can_retry(store):
    execute(store, Fake({None: page(item("a"))}))

    class Broken(LocalAnalysis):
        def analyze(self, _item):
            from bookmark_atlas.models import AtlasError

            raise AtlasError("model offline")

    assert analyze_pending(store, Broken())["failed"] == 1
    assert store.stats()["items"] == 1
    assert analyze_pending(store, LocalAnalysis())["analyzed"] == 1
    assert store.db.execute("SELECT COUNT(*) FROM analysis_failures").fetchone()[0] == 0


def test_chinese_search_and_source_links(store):
    execute(store, Fake({None: page(item("a", "中文教程"), item("b", "Other"))}))
    assert len(store.items("中文")) == 1
    assert LocalAnalysis().analyze(
        item("a", links=["https://github.com/example/project"]).to_dict()
    )["projects"] == ["https://github.com/example/project"]


def test_x_empty_terminal_page_may_keep_cursor(store):
    fake = Fake({None: page(item("a"), cursor="tail"), "tail": page(cursor="tail")})
    result = execute(store, fake)
    assert result["status"] == "complete"
    assert store.checkpoint("x", "api") is None
    assert store.stats()["items"] == 1


def test_auto_fallback_when_identity_works_but_bookmark_scope_missing(store):
    api = Fake({None: AuthError("bookmark scope unavailable")})
    web = Fake({None: page(item("a"))}, name="web")
    result = sync(store, {"api": lambda: api, "web": lambda: web}, delay=0)
    assert result["adapter"] == "web"
    assert api.closed and web.closed
    assert store.stats()["items"] == 1


def test_volatile_media_counters_do_not_reanalyze_or_duplicate_media(store):
    first = item(
        "a", media=[{"media_key": "m1", "type": "video", "additional_media_info": {"views": 1}}]
    )
    execute(store, Fake({None: page(first)}))
    assert analyze_pending(store, LocalAnalysis())["analyzed"] == 1
    second = item(
        "a", media=[{"media_key": "m1", "type": "video", "additional_media_info": {"views": 2}}]
    )
    assert execute(store, Fake({None: page(second)}))["updated"] == 0
    assert analyze_pending(store, LocalAnalysis())["analyzed"] == 0
    assert len(store.items()[0]["document"]["media"]) == 1
