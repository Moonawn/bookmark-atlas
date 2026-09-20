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
from urllib.parse import urlsplit

import httpx

from .config import atomic_write, private_dir
from .http import network_client
from .models import AtlasError, digest, now, source_key, stable_media_url
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
    """What this item's media should become on disk, in order.

    Media belonging to a quoted post carries that through as `quoted`, so a
    downloaded file can still be told apart from the bookmarker's own.
    """
    found = []
    for media in document.get("media", []):
        thumb = media.get("media_url_https")
        quoted = bool(media.get("quoted"))
        if video := best_video(media):
            found.append(
                {
                    "kind": "video",
                    "url": video,
                    "thumbnail": thumb,
                    "limit": video_limit,
                    "quoted": quoted,
                }
            )
        elif thumb:
            wechat = document.get("site") == "wechat"
            found.append(
                {
                    "kind": "photo",
                    "url": thumb if wechat else f"{thumb}?name=orig",
                    "thumbnail": thumb if wechat else f"{thumb}?name=small",
                    "limit": PHOTO_LIMIT,
                    "quoted": quoted,
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
    kwargs = {}
    if urlsplit(url).hostname in {"mmbiz.qpic.cn", "mmbiz.qlogo.cn"}:
        kwargs["headers"] = {"Referer": "https://mp.weixin.qq.com/"}
    with client.stream("GET", url, **kwargs) as response:
        if response.status_code != 200:
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
        written, content_type = download(client, url, part, PHOTO_LIMIT)
    except (AtlasError, OSError, httpx.HTTPError):
        part.unlink(missing_ok=True)
        return None
    if written > PHOTO_LIMIT:
        return None
    if not content_type.startswith("image/") or content_type not in SUFFIXES:
        part.unlink(missing_ok=True)
        return None
    final = target / f"{name}-thumb{SUFFIXES[content_type]}"
    part.replace(final)
    return final.name


def existing_file(target: Path, name: str | None) -> bool:
    return bool(name and Path(name).name == name and (target / name).is_file())


def matching_record(records: list[dict], item: dict) -> dict:
    identity = stable_media_url(item["url"])
    return next(
        (
            record
            for record in records
            if stable_media_url(record.get("source_url") or record.get("original") or "")
            == identity
        ),
        {},
    )


def complete(record: dict, item: dict, target: Path, fetch_videos: bool) -> bool:
    if record.get("error"):
        return False
    if existing_file(target, record.get("file")):
        return True
    if not existing_file(target, record.get("thumbnail")):
        return False
    if record.get("skipped") == "video_not_downloaded":
        return not fetch_videos
    if record.get("skipped") == "over_limit":
        return item["limit"] <= record.get(
            "limit", VIDEO_LIMIT if item["kind"] == "video" else PHOTO_LIMIT
        )
    return False


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
    """Resume each asset independently; retain successful files and retry failures.

    Video binaries are opt-in. A larger cap retries earlier oversized assets.
    Index progress is saved after each item, including failures with provenance.
    """
    target = home / "media"
    if not dry_run:
        private_dir(target)
    index = load_index(target)
    wanted = set(items) if items else None
    video_limit = video_limit_mb * 1024 * 1024 if video_limit_mb else VIDEO_LIMIT
    rows = []
    for row in store.items():
        document = row["document"]
        key = source_key(document)
        if wanted is not None and key not in wanted:
            continue
        records = index.get(key, {}).get("files", [])
        if any(
            not complete(matching_record(records, item), item, target, fetch_videos)
            for item in targets(document, video_limit)
        ):
            rows.append(row)
    if limit:
        rows = rows[:limit]

    downloaded = oversized = frames = failed = reused = 0
    bytes_total = 0
    touched: list[dict] = []
    with network_client(follow_redirects=True) as client:
        for row in rows:
            document = row["document"]
            key = source_key(document)
            previous = index.get(key, {}).get("files", [])
            files: list[dict] = []
            # Preserve names of reused assets even if the source order changed.
            for number, item in enumerate(targets(document, video_limit), start=1):
                old = matching_record(previous, item)
                base = {
                    "n": number,
                    "kind": item["kind"],
                    "quoted": item["quoted"],
                    "source_url": item["url"],
                }
                if complete(old, item, target, fetch_videos):
                    files.append({**old, **base})
                    reused += 1
                    continue
                skip_video = item["kind"] == "video" and not fetch_videos
                if dry_run:
                    files.append({**base, "plan": "thumbnail" if skip_video else "download"})
                    continue
                # Asset identity, rather than list position, avoids overwriting a
                # different reused file when a post gains/reorders its images.
                name = f"{key}-{digest(stable_media_url(item['url']))[:16]}"
                part = target / f"{name}.part"
                record = dict(base)
                if skip_video:
                    record.update(skipped="video_not_downloaded", original=item["url"])
                    if thumb := keep_thumbnail(client, item, name, target):
                        record["thumbnail"] = thumb
                        frames += 1
                    else:
                        record["error"] = "视频封面下载失败；下次重试。"
                        failed += 1
                else:
                    try:
                        written, content_type = download(client, item["url"], part, item["limit"])
                        if written > item["limit"]:
                            oversized += 1
                            record.update(
                                skipped="over_limit",
                                original=item["url"],
                                bytes=written,
                                limit=item["limit"],
                            )
                            if thumb := keep_thumbnail(client, item, name, target):
                                record["thumbnail"] = thumb
                            else:
                                record["error"] = "文件超限且缩略图下载失败；下次重试。"
                                failed += 1
                        else:
                            if content_type not in SUFFIXES:
                                raise AtlasError("媒体响应不是支持的图片或视频格式。")
                            final = f"{name}{SUFFIXES[content_type]}"
                            part.replace(target / final)
                            record.update(file=final, bytes=written)
                            downloaded += 1
                            bytes_total += written
                    except (AtlasError, OSError, httpx.HTTPError) as exc:
                        failed += 1
                        # Transport errors can include signed URLs; keep diagnostics safe.
                        record["error"] = (
                            str(exc) if isinstance(exc, AtlasError) else type(exc).__name__
                        )
                        # Preserve an earlier thumbnail when upgrading to the video fails.
                        if existing_file(target, old.get("thumbnail")):
                            record["thumbnail"] = old["thumbnail"]
                    finally:
                        part.unlink(missing_ok=True)
                files.append(record)
            if not dry_run:
                index[key] = {
                    "item_id": document["item_id"],
                    "url": document.get("url", ""),
                    "author": document.get("author", ""),
                    "fetched_at": now(),
                    "files": files,
                }
                save_index(target, index)
            touched.append({"key": key, "files": files})

    stored = sum(
        entry["bytes"]
        for record in index.values()
        for entry in record.get("files", [])
        if entry.get("file") and entry.get("bytes")
    )
    return {
        "items": len(rows),
        "downloaded": downloaded,
        "video_frames": frames,
        "oversized": oversized,
        "failed": failed,
        "reused": reused,
        "bytes": bytes_total,
        "stored_total_bytes": stored,
        "over_total_warn": stored > TOTAL_WARN,
        "applied": not dry_run,
        "sample": touched[:3],
    }
