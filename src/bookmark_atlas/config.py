from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .models import AtlasError


def home_path(value: str | None = None) -> Path:
    return (
        Path(value or os.getenv("ATLAS_HOME", "~/.local/share/bookmark-atlas"))
        .expanduser()
        .resolve()
    )


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def atomic_write(path: Path, text: str) -> None:
    private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".atlas-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_credentials(home: Path) -> dict:
    path = home / "credentials.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError) as exc:
        raise AtlasError("无法读取本地 credentials.json，请检查文件格式和权限。") from exc


def save_credentials(home: Path, values: dict) -> None:
    current = read_credentials(home)
    current.update(values)
    atomic_write(home / "credentials.json", json.dumps(current, indent=2))
