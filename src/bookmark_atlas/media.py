"""Fetch the media binaries that archived items point at.

The archive records media metadata, not files, so a deleted post takes its
images with it. This downloads the binaries — but only the ones worth keeping
whole.

Two rules shape what lands on disk:

- **Naming carries provenance.** Every file is named after the source key of
  the post it came from, and `index.json` records the original URL, so a file
  can always be traced back to where it came from.
- **An oversized file is not silently dropped.** The post's own thumbnail is
  fetched in its place and the skip is recorded with the original URL, so the
  decision stays visible and can be revisited later.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import atomic_write, private_dir
from .http import network_client
from .models import AtlasError, now, source_key
from .store import Store

PHOTO_LIMIT = 5 * 1024 * 1024
VIDEO_LIMIT = 100 * 1024 * 1024
TOTAL_WARN = 10 * 1024 * 1024 * 1024

# Chosen from the response content type so the file opens with the right app.
SUFFIXES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
}


def best_video(media: dict) -> str | None:
    """Highest-bitrate mp4 variant; None when only a streaming manifest exists."""
    variants = [
        variant
        for variant in (media.get("video_info") or {}).get("variants", [])
        if variant.get("content_type") == "video/mp4" and variant.get("url")
    ]
    if not variants:
        return None
    return max(variants, key=lambda variant: variant.get("bitrate") or 0)["url"]


def targets(document: dict, video_limit: int = VIDEO_LIMIT) -> list[dict[str, Any]]:
    """What this item's media should become on disk, in order."""
    found = []
    for media in document.get("media", []):
        thumb = media.get("media_url_https")
        if video := best_video(media):
            found.append({"kind": "video", "url": video, "thumbnail": thumb, "limit": video_limit})
        elif thumb:
            found.append(
                {
                    "kind": "photo",
                    "url": f"{thumb}?name=orig",
                    "thumbnail": f"{thumb}?name=small",
                    "limit": PHOTO_LIMIT,
                }
            )
    return found


def load_index(target: Path) -> dict:
    path = target / "index.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (OSError, ValueError) as exc:
        raise AtlasError("media/index.json 无效；请修复后再下载，避免重复抓取。") from exc


def save_index(target: Path, index: dict) -> None:
    private_dir(target)
    atomic_write(target / "index.json", json.dumps(index, ensure_ascii=False, indent=2))


def download(client, url: str, part: Path, limit: int) -> tuple[int, str]:
    """Stream a file to `part`, abandoning it as soon as it exceeds `limit`.

    Returns (bytes seen, content type). Over-limit files are removed rather
    than kept, so a partial download never masquerades as the real thing.
    """
    with client.stream("GET", url) as response:
        if response.status_code >= 400:
            raise AtlasError(f"媒体请求失败（HTTP {response.status_code}）。")
        content_type = (response.headers.get("content-type") or "").split(";")[0].strip()
        written = 0
        with part.open("wb") as handle:
            for chunk in response.iter_bytes(65536):
                written += len(chunk)
                if written > limit:
                    break
                handle.write(chunk)
    if written > limit:
        part.unlink(missing_ok=True)
    return written, content_type


def keep_thumbnail(client, item: dict, name: str, target: Path) -> str | None:
    """Store a frame in place of an oversized file. Returns its filename."""
    url = item.get("thumbnail")
    if not url:
        return None
    part = target / f"{name}-thumb.part"
    try:
        written, _ = download(client, url, part, PHOTO_LIMIT)
    except (AtlasError, OSError):
        part.unlink(missing_ok=True)
        return None
    if written > PHOTO_LIMIT:
        return None
    final = target / f"{name}-thumb.jpg"
    part.replace(final)
    return final.name


def fetch_media(
    store: Store,
    home: Path,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    fetch_videos: bool = False,
    items: list[str] | None = None,
    video_limit_mb: int | None = None,
) -> dict:
    """Download media for archived items that do not have it yet.

    Videos are not downloaded unless `fetch_videos` is set: a single frame
    tells you what the video was, at a fraction of the size, and one long
    video can outweigh everything else in the archive. The full URL is still
    recorded either way, so the choice can be revisited.

    Naming items explicitly reprocesses them even if already indexed, which is
    how a video that was framed earlier gets fetched in full. `video_limit_mb`
    raises the size cap for this run only.
    """
    target = home / "media"
    if not dry_run:
        private_dir(target)
    index = load_index(target)
    wanted = set(items) if items else None
    rows = []
    for row in store.items():
        document = row["document"]
        if not document.get("media"):
            continue
        key = source_key(document)
        if wanted is not None:
            if key in wanted:
                rows.append(row)
        elif key not in index:
            rows.append(row)
    if limit:
        rows = rows[:limit]
    video_limit = video_limit_mb * 1024 * 1024 if video_limit_mb else VIDEO_LIMIT

    downloaded = oversized = frames = failed = 0
    bytes_total = 0
    touched: list[dict] = []

    with network_client(follow_redirects=True) as client:
        for row in rows:
            document = row["document"]
            key = source_key(document)
            files: list[dict] = []
            for number, item in enumerate(targets(document, video_limit), start=1):
                name = f"{key}-{number}"
                is_video = item["kind"] == "video"
                skip_video = is_video and not fetch_videos
                if dry_run:
                    files.append(
                        {
                            "n": number,
                            "kind": item["kind"],
                            "plan": "thumbnail" if skip_video else "download",
                            "url": item["url"],
                        }
                    )
                    continue
                if skip_video:
                    frames += 1
                    record: dict[str, Any] = {
                        "n": number,
                        "kind": "video",
                        "skipped": "video_not_downloaded",
                        "original": item["url"],
                    }
                    if thumb := keep_thumbnail(client, item, name, target):
                        record["thumbnail"] = thumb
                    files.append(record)
                    continue
                part = target / f"{name}.part"
                try:
                    written, content_type = download(client, item["url"], part, item["limit"])
                except (AtlasError, OSError) as exc:
                    failed += 1
                    files.append({"n": number, "kind": item["kind"], "error": str(exc)})
                    continue

                if written > item["limit"]:
                    # Too large to keep whole. Keep a frame instead so the post
                    # still shows what the file was, and record the full URL.
                    oversized += 1
                    record: dict[str, Any] = {
                        "n": number,
                        "kind": item["kind"],
                        "skipped": "over_limit",
                        "bytes": written,
                        "original": item["url"],
                    }
                    if thumb := keep_thumbnail(client, item, name, target):
                        record["thumbnail"] = thumb
                    files.append(record)
                    continue

                suffix = SUFFIXES.get(content_type, ".bin")
                part.replace(target / f"{name}{suffix}")
                files.append(
                    {
                        "n": number,
                        "kind": item["kind"],
                        "file": f"{name}{suffix}",
                        "source_url": item["url"],
                        "bytes": written,
                    }
                )
                downloaded += 1
                bytes_total += written

            # Only record work that actually finished. An item whose every file
            # errored is left out so the next run retries it, instead of being
            # skipped forever as already done.
            if not dry_run and any(entry.get("file") or entry.get("skipped") for entry in files):
                index[key] = {
                    "item_id": document["item_id"],
                    "url": document.get("url", ""),
                    "author": document.get("author", ""),
                    "fetched_at": now(),
                    "files": files,
                }
            touched.append({"key": key, "files": files})

    if not dry_run and touched:
        save_index(target, index)

    stored = sum(
        entry["bytes"]
        for record in index.values()
        for entry in record.get("files", [])
        if entry.get("bytes") and not entry.get("skipped")
    )
    return {
        "items": len(rows),
        "downloaded": downloaded,
        "video_frames": frames,
        "oversized": oversized,
        "failed": failed,
        "bytes": bytes_total,
        "stored_total_bytes": stored,
        "over_total_warn": stored > TOTAL_WARN,
        "applied": not dry_run,
        "sample": touched[:3],
    }
