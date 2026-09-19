import copy
import json

import httpx
import pytest
from test_core import Fake, item, page
from test_media import add_item, document, install, photo, video

from bookmark_atlas import cli
from bookmark_atlas.media import fetch_media
from bookmark_atlas.models import Identity, Page, stable_media_url
from bookmark_atlas.store import Store, content_digest
from bookmark_atlas.wiki import apply_notes, export_wiki, wiki_status


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path)
    yield value
    value.close()


def test_partial_failure_retries_only_failed_asset(store, tmp_path, monkeypatch):
    requests = []
    fail = True

    def handler(request):
        requests.append(request.url.path)
        if fail and request.url.path.endswith("b.jpg"):
            return httpx.Response(503)
        return httpx.Response(200, content=b"image", headers={"content-type": "image/jpeg"})

    install(monkeypatch, handler)
    add_item(store, [photo("https://pbs.twimg.com/a.jpg"), photo("https://pbs.twimg.com/b.jpg")])
    first = fetch_media(store, tmp_path)
    assert (first["downloaded"], first["failed"]) == (1, 1)
    fail = False
    requests.clear()
    second = fetch_media(store, tmp_path)
    assert (second["downloaded"], second["failed"], second["reused"]) == (1, 0, 1)
    assert requests == ["/b.jpg"]
    assert fetch_media(store, tmp_path)["items"] == 0


def test_missing_file_and_failed_thumbnail_are_retried(store, tmp_path, monkeypatch):
    fail = True

    def handler(request):
        if fail and request.url.path == "/t.jpg":
            raise httpx.ReadTimeout("private signed URL must not appear in report")
        return httpx.Response(200, content=b"image", headers={"content-type": "image/jpeg"})

    install(monkeypatch, handler)
    add_item(store, [video(), photo()])
    assert fetch_media(store, tmp_path)["failed"] == 1
    fail = False
    second = fetch_media(store, tmp_path)
    assert second["video_frames"] == 1 and second["reused"] == 1
    index = json.loads((tmp_path / "media/index.json").read_text())
    files = next(iter(index.values()))["files"]
    (tmp_path / "media" / files[1]["file"]).unlink()
    assert fetch_media(store, tmp_path)["downloaded"] == 1


def test_stream_failure_keeps_progress_and_cleans_partial_files(store, tmp_path, monkeypatch):
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"partial"
            raise httpx.ReadError("sensitive-url")

    def handler(request):
        if request.url.path.endswith("b.jpg"):
            return httpx.Response(
                200, stream=BrokenStream(), headers={"content-type": "image/jpeg"}
            )
        return httpx.Response(200, content=b"ok", headers={"content-type": "image/jpeg"})

    install(monkeypatch, handler)
    add_item(store, [photo("https://pbs.twimg.com/a.jpg")], "1")
    add_item(store, [photo("https://pbs.twimg.com/b.jpg")], "2")
    report = fetch_media(store, tmp_path)
    assert report["downloaded"] == 1 and report["failed"] == 1
    assert "sensitive-url" not in json.dumps(report)
    assert not list((tmp_path / "media").glob("*.part"))
    assert len(json.loads((tmp_path / "media/index.json").read_text())) == 2


def test_existing_frame_upgrades_to_video_without_refetching_photos(store, tmp_path, monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        kind = "video/mp4" if request.url.path.endswith(".mp4") else "image/jpeg"
        return httpx.Response(200, content=b"data", headers={"content-type": kind})

    install(monkeypatch, handler)
    add_item(store, [photo(), video()])
    fetch_media(store, tmp_path)
    seen.clear()
    report = fetch_media(store, tmp_path, fetch_videos=True)
    assert report["downloaded"] == 1 and report["reused"] == 1
    assert seen == ["/v/1.mp4"]


def test_cdn_hash_normalization_is_narrow():
    old = document([video("https://video.twimg.com/ext_tw_video/1/pu/vid/a.mp4?tag=1&v=a")])
    new = copy.deepcopy(old)
    new["media"][0]["video_info"]["variants"][0]["url"] = (
        "https://video.twimg.com/ext_tw_video/1/pu/vid/a.mp4?tag=2&v=b"
    )
    assert content_digest(old) == content_digest(new)
    new["media"].append(photo())
    assert content_digest(old) != content_digest(new)
    assert stable_media_url("https://example.com/a?v=1") == "https://example.com/a?v=1"
    assert stable_media_url("https://pbs.twimg.com/a?name=orig") != stable_media_url(
        "https://pbs.twimg.com/a?name=small"
    )
    assert stable_media_url("https://video.twimg.com/a?format=mp4") != stable_media_url(
        "https://video.twimg.com/a?format=webm"
    )


def test_existing_wiki_receipt_survives_hash_policy_upgrade(store, tmp_path):
    from bookmark_atlas.models import Item

    post = Item(
        "x",
        "1",
        "useful original text",
        "https://x.com/i/status/1",
        media=[video("https://video.twimg.com/a.mp4?tag=1&v=a")],
    )
    owner = Identity("x", "owner")
    store.bind_owner(owner)
    run_id = store.start("x", "web")
    store.commit_page(owner, "web", run_id, Page([post], None, {}), None, None)
    # Existing releases used a different hash representation.
    store.db.execute("UPDATE items SET content_hash='legacy-receipt'")
    store.db.commit()
    target = tmp_path / "wiki"
    export_wiki(store, target, "local-v1")
    source = json.loads((target / "queue.json").read_text())["items"][0]
    apply_notes(
        store,
        target,
        {
            "notes": [
                {
                    "id": "sample",
                    "title": "Sample",
                    "body": "Useful explanation.",
                    "sources": [{"key": source["key"], "hash": source["hash"]}],
                }
            ]
        },
    )
    post.media[0]["video_info"]["variants"][0]["url"] = "https://video.twimg.com/a.mp4?tag=2&v=b"
    assert store.commit_page(owner, "web", run_id, Page([post], None, {}), None, None) == (0, 0)
    assert wiki_status(store, target)["pending"] == 0
    post.text += " new substantive content"
    assert store.commit_page(owner, "web", run_id, Page([post], None, {}), None, None) == (0, 1)
    assert wiki_status(store, target)["pending"] == 1


def test_sync_inherits_media_setting_and_exports_even_when_media_fails(tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text(json.dumps({"media": "images", "organization": "wiki"}))
    monkeypatch.setattr(
        cli, "factories_for", lambda *_: {"api": lambda: Fake({None: page(item("1"))})}
    )
    monkeypatch.setattr(cli, "run_watches", lambda *_args, **_kw: {})
    called = []

    def fetch(store, home):
        called.append(home)
        return {"failed": 1, "downloaded": 0}

    monkeypatch.setattr(cli, "fetch_media", fetch)
    result = cli.run(cli.parser().parse_args(["--home", str(tmp_path), "sync"]))
    assert called == [tmp_path] and result["media"]["failed"] == 1
    assert (tmp_path / "exports/bookmarks.json").is_file()
    assert (tmp_path / "wiki/queue.json").is_file()
    called.clear()
    cli.run(cli.parser().parse_args(["--home", str(tmp_path), "sync", "--media", "none"]))
    assert called == []
