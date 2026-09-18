import json

import httpx
import pytest

from bookmark_atlas import media
from bookmark_atlas.media import best_video, fetch_media, targets
from bookmark_atlas.store import Store


def photo(url="https://pbs.twimg.com/media/AAA.jpg"):
    return {"type": "photo", "media_url_https": url}


def video(mp4="https://video.twimg.com/v/1.mp4", bitrate=1000, thumb="https://pbs.twimg.com/t.jpg"):
    return {
        "type": "video",
        "media_url_https": thumb,
        "video_info": {
            "variants": [
                {"content_type": "video/mp4", "url": mp4, "bitrate": bitrate},
                {"content_type": "video/mp4", "url": mp4 + "?low", "bitrate": 100},
                {"content_type": "application/x-mpegURL", "url": "https://hls.m3u8"},
            ]
        },
    }


def document(media_items, item_id="1"):
    return {
        "site": "x",
        "item_id": item_id,
        "text": "hello",
        "url": f"https://x.com/i/status/{item_id}",
        "author": "someone",
        "media": media_items,
    }


def test_targets_prefers_original_and_highest_bitrate():
    """Photos fetch at original size; videos pick the richest mp4, not HLS."""
    photos = targets(document([photo()]))
    assert photos[0]["url"].endswith("?name=orig")
    assert photos[0]["thumbnail"].endswith("?name=small")

    videos = targets(document([video()]))
    assert videos[0]["url"] == "https://video.twimg.com/v/1.mp4"
    assert best_video(video()) == "https://video.twimg.com/v/1.mp4"


def test_best_video_returns_none_without_mp4():
    assert (
        best_video({"video_info": {"variants": [{"content_type": "application/x-mpegURL"}]}})
        is None
    )


@pytest.fixture
def archive(tmp_path):
    store = Store(tmp_path)
    yield store
    store.close()


def install(monkeypatch, handler):
    """Route media downloads through a mock transport."""
    monkeypatch.setattr(
        media,
        "network_client",
        lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ),
    )


def add_item(store, media_items, item_id="1"):
    store.db.execute(
        "INSERT INTO items VALUES(?,?,?,?,?,?)",
        ("x", item_id, json.dumps(document(media_items, item_id)), "h", "t", "t"),
    )
    store.db.commit()


def test_oversized_video_keeps_a_thumbnail_and_records_the_skip(archive, tmp_path, monkeypatch):
    """A file too large to keep whole still leaves a visible trace."""
    big = b"x" * (media.VIDEO_LIMIT + 1)

    def handler(request):
        if "t.jpg" in str(request.url):
            return httpx.Response(200, content=b"thumb", headers={"content-type": "image/jpeg"})
        return httpx.Response(200, content=big, headers={"content-type": "video/mp4"})

    install(monkeypatch, handler)
    add_item(archive, [video()])

    report = fetch_media(archive, tmp_path, fetch_videos=True)
    assert report["oversized"] == 1
    assert report["downloaded"] == 0

    index = json.loads((tmp_path / "media/index.json").read_text())
    entry = index[next(iter(index))]["files"][0]
    assert entry["skipped"] == "over_limit"
    assert entry["original"] == "https://video.twimg.com/v/1.mp4"  # provenance survives
    assert entry["thumbnail"].endswith("-thumb.jpg")
    assert (tmp_path / "media" / entry["thumbnail"]).is_file()


def test_failed_items_are_not_recorded_so_they_retry(archive, tmp_path, monkeypatch):
    """An item whose download errored must not be marked as already done."""
    install(monkeypatch, lambda request: httpx.Response(503))
    add_item(archive, [photo()])

    report = fetch_media(archive, tmp_path)
    assert report["failed"] == 1
    index = tmp_path / "media/index.json"
    assert json.loads(index.read_text()) == {}  # nothing recorded, so the next run retries


def test_videos_are_framed_not_downloaded_by_default(archive, tmp_path, monkeypatch):
    """A video costs little to identify: keep a frame, record where it lives."""
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=b"frame", headers={"content-type": "image/jpeg"})

    install(monkeypatch, handler)
    add_item(archive, [video(), photo()])

    report = fetch_media(archive, tmp_path)
    assert report["video_frames"] == 1
    assert report["downloaded"] == 1  # the photo still comes down whole
    assert not any("/v/1.mp4" in url for url in seen)  # video bytes never requested

    index = json.loads((tmp_path / "media/index.json").read_text())
    files = index[next(iter(index))]["files"]
    assert files[0]["skipped"] == "video_not_downloaded"
    assert files[0]["original"] == "https://video.twimg.com/v/1.mp4"
    assert files[0]["thumbnail"].endswith("-thumb.jpg")
    assert files[1]["file"].endswith(".jpg")


def test_records_provenance_for_every_download(archive, tmp_path, monkeypatch):
    """Each file on disk can be traced back to the post and URL it came from."""
    install(
        monkeypatch,
        lambda request: httpx.Response(200, content=b"data", headers={"content-type": "image/png"}),
    )
    add_item(archive, [photo("https://pbs.twimg.com/media/BBB.png")])

    fetch_media(archive, tmp_path)
    index = json.loads((tmp_path / "media/index.json").read_text())
    key = next(iter(index))
    entry = index[key]
    assert entry["item_id"] == "1"
    assert entry["url"] == "https://x.com/i/status/1"
    record = entry["files"][0]
    assert record["source_url"].endswith("BBB.png?name=orig")
    assert (tmp_path / "media" / record["file"]).is_file()
    assert record["file"].startswith(key)  # filename carries the source key


def test_targets_marks_media_belonging_to_a_quoted_post():
    own = photo("https://pbs.twimg.com/media/mine.jpg")
    theirs = {**photo("https://pbs.twimg.com/media/theirs.jpg"), "quoted": True}
    found = targets(document([own, theirs]))
    assert [t["quoted"] for t in found] == [False, True]


def test_media_added_later_is_fetched_even_though_the_item_is_indexed(
    archive, tmp_path, monkeypatch
):
    """A parser improvement that finds more media must not be skipped as done."""
    install(
        monkeypatch,
        lambda request: httpx.Response(200, content=b"data", headers={"content-type": "image/png"}),
    )
    add_item(archive, [photo("https://pbs.twimg.com/media/one.png")])
    fetch_media(archive, tmp_path)
    assert len(next(iter(load_index(tmp_path).values()))["files"]) == 1

    # The same post re-archived with an added quoted image, as a replay does.
    archive.db.execute(
        "INSERT INTO items VALUES(?,?,?,?,?,?) ON CONFLICT(site,item_id) DO UPDATE SET document=excluded.document",
        (
            "x",
            "1",
            json.dumps(
                document(
                    [
                        photo("https://pbs.twimg.com/media/one.png"),
                        {**photo("https://pbs.twimg.com/media/two.png"), "quoted": True},
                    ]
                )
            ),
            "h",
            "t",
            "t",
        ),
    )
    archive.db.commit()

    # Reprocessing re-fetches the item's whole media list, overwriting the
    # files already on disk under the same deterministic names.
    result = fetch_media(archive, tmp_path)
    assert result["items"] == 1 and result["downloaded"] == 2
    assert [f["quoted"] for f in next(iter(load_index(tmp_path).values()))["files"]] == [False, True]


def load_index(tmp_path):
    return json.loads((tmp_path / "media/index.json").read_text())
