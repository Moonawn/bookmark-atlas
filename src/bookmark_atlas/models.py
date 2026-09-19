from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def stable_media_url(url: str) -> str:
    """Compare X CDN assets without their delivery/signing parameters.

    Keep unknown hosts and parameters, including image name/format selectors.
    The original URL is always retained for downloading.
    """
    parsed = urlsplit(url)
    if parsed.hostname not in ("video.twimg.com", "pbs.twimg.com"):
        return url
    volatile = {"expires", "signature", "key-pair-id", "policy"}
    if parsed.hostname == "video.twimg.com":
        volatile |= {"tag", "v"}
    pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in volatile and not key.lower().startswith("x-amz-")
    ]
    return urlunsplit(parsed._replace(query=urlencode(sorted(pairs))))


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def source_key(item: dict) -> str:
    """Stable filename-safe identifier for an archived item.

    Derived from site and item id, so the same post always maps to the same
    key regardless of parser version — which is what lets a media file on
    disk, a wiki source page and a manifest entry name the same post.
    """
    return digest([item["site"], item["item_id"]])[:24]


@dataclass(frozen=True)
class Identity:
    site: str
    account_id: str
    username: str = ""


@dataclass
class Item:
    site: str
    item_id: str
    text: str
    url: str
    author_id: str = ""
    author: str = ""
    published_at: str = ""
    links: list[str] = field(default_factory=list)
    media: list[dict] = field(default_factory=list)
    language: str = ""
    kind: str = "post"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Page:
    items: list[Item]
    next_cursor: str | None
    raw: dict


class Adapter(Protocol):
    name: str

    def identity(self) -> Identity: ...
    def page(self, cursor: str | None = None) -> Page: ...
    def close(self) -> None: ...


class AtlasError(Exception):
    """Safe-to-display operational message. Never include raw response bodies."""


class AuthError(AtlasError):
    pass


class UnavailableError(AtlasError):
    pass


class ParseError(AtlasError):
    pass


class RateLimitError(AtlasError):
    def __init__(self, retry_at: float):
        self.retry_at = retry_at
        super().__init__(
            f"X 限流，最早重试时间 {datetime.fromtimestamp(retry_at, UTC).isoformat()}"
        )


class OwnerMismatch(AtlasError):
    pass
