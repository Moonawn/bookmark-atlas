import json
from datetime import datetime

import pytest
from test_core import Fake, execute, item, page

from bookmark_atlas import cli
from bookmark_atlas.analysis import LocalAnalysis, analyze_pending
from bookmark_atlas.config import atomic_write
from bookmark_atlas.models import AtlasError, source_key
from bookmark_atlas.preferences import load, next_daily, setup, validate
from bookmark_atlas.store import Store
from bookmark_atlas.wiki import apply_notes, export_wiki, wiki_status


def stamp(iso):
    return datetime.fromisoformat(iso).timestamp()


@pytest.mark.parametrize(
    "start, at, zone, expected",
    [
        ("2026-09-07T20:59:00+08:00", "21:00", "Asia/Shanghai", "2026-09-07T21:00:00+08:00"),
        ("2026-09-07T21:00:00+08:00", "21:00", "Asia/Shanghai", "2026-09-08T21:00:00+08:00"),
        ("2026-03-08T01:00:00-05:00", "02:30", "America/New_York", "2026-03-09T02:30:00-04:00"),
        ("2026-11-01T01:45:00-04:00", "01:30", "America/New_York", "2026-11-02T01:30:00-05:00"),
    ],
)
def test_daily_time_and_dst(start, at, zone, expected):
    assert next_daily(at, zone, stamp(start)) == stamp(expected)


@pytest.mark.parametrize(
    "values",
    [
        {"at": "25:61"},
        {"interval": True},
        {"timezone": "bad/zone"},
        {"summary": "ollama"},
        {"summary": "agent"},
        {"token": "no"},
    ],
)
def test_invalid_settings(values):
    with pytest.raises(AtlasError):
        validate(values)


def test_setup_persists_without_starting_work_and_failure_preserves_config(tmp_path):
    replies = iter(["web", "chrome", "daily", "21:00", "Asia/Shanghai", "wiki", "agent"])
    setup(tmp_path, lambda _: next(replies))
    assert load(tmp_path)["summary"] == "agent"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.json"]
    before = (tmp_path / "settings.json").read_bytes()
    replies = iter(["web", "chrome", "daily", "25:00", "Asia/Shanghai", "wiki", "agent"])
    with pytest.raises(AtlasError):
        setup(tmp_path, lambda _: next(replies))
    assert (tmp_path / "settings.json").read_bytes() == before
    assert (tmp_path / "settings.json").stat().st_mode & 0o777 == 0o600


def test_cli_saved_preferences_and_explicit_override(tmp_path, monkeypatch):
    atomic_write(
        tmp_path / "settings.json",
        json.dumps(validate({"mode": "web", "organization": "wiki", "summary": "agent"})),
    )

    def capture(args, _home):
        return {"mode": args.mode, "organization": args.organization, "model": args.ollama_model}

    monkeypatch.setattr(cli, "cycle", capture)
    assert cli.run(cli.parser().parse_args(["--home", str(tmp_path), "sync"])) == {
        "mode": "web",
        "organization": "wiki",
        "model": None,
    }
    assert (
        cli.run(cli.parser().parse_args(["--home", str(tmp_path), "sync", "--mode", "api"]))["mode"]
        == "api"
    )
    assert (
        cli.run(
            cli.parser().parse_args(["--home", str(tmp_path), "serve", "--at", "21:00", "--once"])
        )["organization"]
        == "wiki"
    )


def test_daily_runner_waits_before_first_cycle(tmp_path, monkeypatch):
    clock = [stamp("2026-09-07T20:59:00+08:00")]
    monkeypatch.setattr(cli.time, "time", lambda: clock[0])
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def capture(_args, _home):
        assert clock[0] == stamp("2026-09-07T21:00:00+08:00")
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "cycle", capture)
    with pytest.raises(KeyboardInterrupt):
        cli.run(cli.parser().parse_args(["--home", str(tmp_path), "serve", "--at", "21:00"]))


@pytest.fixture
def archive(tmp_path):
    store = Store(tmp_path)
    execute(store, Fake({None: page(item("a", "AI 教程"), item("b", "研究方法"))}))
    analyze_pending(store, LocalAnalysis())
    yield store, tmp_path / "wiki"
    store.close()


def payload(queue, slug="knowledge-note"):
    return {
        "notes": [
            {
                "id": slug,
                "title": "知识笔记",
                "body": "这是资料中的主张，仍需核对。",
                "sources": [{"key": r["key"], "hash": r["hash"]} for r in queue],
            }
        ]
    }


def test_wiki_batch_and_repeat_preserve_personal_notes(archive):
    store, target = archive
    assert export_wiki(store, target, LocalAnalysis.name)["pending"] == 2
    atomic_write(target / "personal" / "my-note.md", "人类笔记")
    queue = json.loads((target / "queue.json").read_text())["items"]
    apply_notes(store, target, payload(queue))
    assert export_wiki(store, target, LocalAnalysis.name)["pending"] == 0
    assert (target / "personal/my-note.md").read_text() == "人类笔记"
    assert all((target / r["source_file"]).is_file() for r in queue)
    assert all(r["key"] in (target / "notes/knowledge-note.md").read_text() for r in queue)
    (target / "notes/knowledge-note.md").unlink()
    assert export_wiki(store, target, LocalAnalysis.name)["pending"] == 2


def test_wiki_rejects_stale_source_and_validates_whole_batch(archive):
    store, target = archive
    export_wiki(store, target, LocalAnalysis.name)
    queue = json.loads((target / "queue.json").read_text())["items"]
    data = payload(queue)
    bad = payload(queue, "../../escape")["notes"][0]
    data["notes"].append(bad)
    with pytest.raises(AtlasError):
        apply_notes(store, target, data)
    assert list((target / "notes").iterdir()) == []
    execute(store, Fake({None: page(item("a", "AI 教程发生了新变化"))}))
    with pytest.raises(AtlasError, match="已变化"):
        apply_notes(store, target, payload(queue))
    assert not (target / "manifest.json").exists()


def test_wiki_update_cannot_drop_existing_sources(archive):
    store, target = archive
    export_wiki(store, target, LocalAnalysis.name)
    queue = json.loads((target / "queue.json").read_text())["items"]
    apply_notes(store, target, payload(queue))
    with pytest.raises(AtlasError, match="全部来源"):
        apply_notes(store, target, payload(queue[:1]))


def test_wiki_escapes_active_content(archive):
    store, target = archive
    export_wiki(store, target, LocalAnalysis.name)
    queue = json.loads((target / "queue.json").read_text())["items"]
    data = payload(queue)
    data["notes"][0]["body"] = "<script>alert(1)</script> ![image](https://evil.test)"
    apply_notes(store, target, data)
    text = (target / "notes/knowledge-note.md").read_text()
    assert "<script>" not in text and "![image]" not in text


def test_changed_source_must_update_all_related_notes(archive):
    store, target = archive
    export_wiki(store, target, LocalAnalysis.name)
    queue = json.loads((target / "queue.json").read_text())["items"]
    both = payload(queue)
    both["notes"] += payload(queue, "second-concept")["notes"]
    apply_notes(store, target, both)
    execute(store, Fake({None: page(item("a", "AI 教程更改内容需要同步其他概念"))}))
    export_wiki(store, target, LocalAnalysis.name)
    fresh = json.loads((target / "queue.json").read_text())["items"]
    refs = {r["key"]: r for r in queue}
    refs.update({r["key"]: r for r in fresh})
    with pytest.raises(AtlasError, match="全部知识笔记"):
        apply_notes(store, target, payload(list(refs.values())))


def test_wiki_source_pages_reach_media_from_one_level_deeper(archive):
    """Wiki sources sit under wiki/sources/items, so media is three levels up."""
    store, target = archive
    key = source_key(next(iter(store.items()))["document"])
    media = target.parent / "media"
    media.mkdir()
    (media / f"{key}-1.jpg").write_bytes(b"photo")
    (media / "index.json").write_text(
        json.dumps({key: {"files": [{"n": 1, "kind": "photo", "file": f"{key}-1.jpg"}]}})
    )

    export_wiki(store, target, LocalAnalysis.name)
    page = (target / "sources/items" / f"{key}.md").read_text()
    assert f"![photo](../../../media/{key}-1.jpg)" in page


def test_wiki_status_never_drifts_from_export_queue(archive):
    """status 的积压数必须与 export_wiki 的队列长度一致：两处共用同一判据。"""
    store, target = archive
    assert wiki_status(store, target) == {"pending": 2, "last_compile": None}
    export_wiki(store, target, LocalAnalysis.name)
    assert wiki_status(store, target)["pending"] == 2
    queue = json.loads((target / "queue.json").read_text())["items"]
    apply_notes(store, target, payload(queue))
    compiled = wiki_status(store, target)
    assert compiled["pending"] == 0
    assert compiled["last_compile"] is not None
    (target / "notes/knowledge-note.md").unlink()
    assert (
        wiki_status(store, target)["pending"]
        == export_wiki(store, target, LocalAnalysis.name)["pending"]
        == 2
    )


def test_status_recomputes_wiki_backlog_without_writing(archive):
    """status 重算积压，既不依赖也可能过期的 queue.json，也不生成它。"""
    store, target = archive
    export_wiki(store, target, LocalAnalysis.name)
    (target / "queue.json").unlink()
    result = cli.run(cli.parser().parse_args(["--home", str(target.parent), "status"]))
    assert result["wiki"]["pending"] == 2
    assert result["wiki"]["last_compile"] is None
    assert not (target / "queue.json").exists()
