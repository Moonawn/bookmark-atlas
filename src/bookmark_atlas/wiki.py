"""Source-preserving Wiki export and validated Agent handoff."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from .config import atomic_write, private_dir
from .export import export_markdown, safe_text
from .models import AtlasError, digest, now


def source_key(item):
    return digest([item["site"], item["item_id"]])[:24]


def manifest_for(target):
    path = target / "manifest.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError("object required")
        for entry in value.values():
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("hash"), str)
                or not isinstance(entry.get("notes"), list)
                or not entry["notes"]
                or not all(
                    isinstance(n, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", n)
                    for n in entry["notes"]
                )
            ):
                raise ValueError("invalid entry")
        return value
    except (OSError, ValueError) as exc:
        raise AtlasError("Wiki manifest 无效；请保留文件并修复，避免重复覆盖笔记。") from exc


def export_wiki(store, target: Path, engine: str):
    private_dir(target)
    export_markdown(store, target / "sources", engine)
    private_dir(target / "notes")
    private_dir(target / "personal")
    manifest = manifest_for(target)
    queue = []
    for row in store.items():
        item = row["document"]
        key = source_key(item)
        prior = manifest.get(key, {})
        if (
            prior.get("hash") == row["content_hash"]
            and prior.get("notes")
            and all((target / "notes" / f"{n}.md").is_file() for n in prior["notes"])
        ):
            continue
        queue.append(
            {
                "key": key,
                "hash": row["content_hash"],
                "source_file": f"sources/items/{key}.md",
                **{k: item[k] for k in ("author", "text", "url", "links")},
            }
        )
    atomic_write(
        target / "queue.json",
        json.dumps({"schema_version": 1, "items": queue}, ensure_ascii=False, indent=2),
    )
    index = [
        "# 收藏 Wiki",
        "",
        "[原文与主题索引](sources/index.md) · [最近入库](sources/recent.md)",
        "",
        "## 知识笔记",
        "",
    ]
    for p in sorted((target / "notes").glob("*.md")):
        first = p.read_text().splitlines()
        label = first[0][2:] if first and first[0].startswith("# ") else safe_text(p.stem)
        index.append(f"- [{label}](notes/{p.name})")
    index += [
        "",
        f"待 Agent 整理：{len(queue)} 条。",
        "",
        "人工笔记放在 personal/，程序不会修改该目录中的内容。",
        "",
    ]
    atomic_write(target / "index.md", "\n".join(index))
    return {
        "path": str(target / "index.md"),
        "pending": len(queue),
        "queue": str(target / "queue.json"),
    }


def apply_notes(store, target: Path, payload: dict):
    """Validate the whole batch before writes. Publish progress after note writes."""
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("notes"), list)
        or not payload["notes"]
    ):
        raise AtlasError("Wiki 输入需要非空 notes 数组。")
    rows = {source_key(r["document"]): r for r in store.items()}
    manifest = manifest_for(target)
    outputs, handled = {}, defaultdict(list)
    for note in payload["notes"]:
        if not isinstance(note, dict):
            raise AtlasError("Wiki 笔记必须是对象。")
        slug = note.get("id")
        if (
            not isinstance(slug, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", slug)
            or slug in outputs
        ):
            raise AtlasError("Wiki 笔记 ID 无效或重复。")
        if not all(isinstance(note.get(k), str) and note[k].strip() for k in ("title", "body")):
            raise AtlasError("Wiki 笔记需要 title 和 body。")
        refs = note.get("sources")
        if not isinstance(refs, list) or not refs:
            raise AtlasError("每条知识笔记必须附上来源。")
        lines = [
            f"# {safe_text(note['title'])}",
            "",
            "自动整理 · 待核对原文",
            "",
            safe_text(note["body"]),
            "",
            "## 来源",
            "",
        ]
        keys = set()
        for ref in refs:
            if not isinstance(ref, dict) or not isinstance(ref.get("key"), str):
                raise AtlasError("Wiki 来源格式无效。")
            key = ref["key"]
            row = rows.get(key)
            if not row or ref.get("hash") != row["content_hash"]:
                raise AtlasError("Wiki 来源不存在或原文已变化，请重新读取 queue.json。")
            if key in keys:
                continue
            keys.add(key)
            lines.append(
                f"- [{safe_text(row['document']['author'] or key)}](../sources/items/{key}.md)"
            )
            handled[key].append(slug)
        for key, entry in manifest.items():
            if slug in entry["notes"] and key not in keys:
                raise AtlasError("更新已有知识笔记时需要保留它的全部来源。")
        outputs[slug] = "\n".join(lines) + "\n"
    for key, slugs in handled.items():
        prior = manifest.get(key, {})
        if prior.get("hash") not in (None, rows[key]["content_hash"]) and not set(
            prior["notes"]
        ).issubset(slugs):
            raise AtlasError("原文已变化，请一并更新与该来源关联的全部知识笔记。")
    private_dir(target / "notes")
    for slug, content in outputs.items():
        atomic_write(target / "notes" / f"{slug}.md", content)
    for key, slugs in handled.items():
        prior = manifest.get(key, {})
        if prior.get("hash") == rows[key]["content_hash"]:
            slugs = sorted(set(slugs + prior.get("notes", [])))
        manifest[key] = {"hash": rows[key]["content_hash"], "notes": slugs}
    atomic_write(target / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    receipt = {"at": now(), "notes": list(outputs), "sources": len(handled)}
    atomic_write(target / "last-compile.json", json.dumps(receipt, ensure_ascii=False, indent=2))
    return receipt
