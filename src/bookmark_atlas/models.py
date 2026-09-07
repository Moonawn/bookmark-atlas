from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


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
