"""Source-preserving Wiki export and validated Agent handoff."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from .config import atomic_write, private_dir
from .export import export_markdown, local_media, safe_text
from .models import AtlasError, now, source_key
from .taxonomy import classify, load_taxonomy

# Every note carries this line under its title, before the real body. Written
# and read in one place so the two never drift apart.
NOTE_MARKER = "自动整理 · 待核对原文"


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


def _awaiting_notes(row, manifest, target):
    """True when an item still needs Agent notes.

    Covers never-compiled sources, content that changed since the last compile,
    and a note file the manifest records but that is no longer on disk.
    """
    prior = manifest.get(source_key(row["document"]), {})
    if prior.get("hash") != row["content_hash"] or not prior.get("notes"):
        return True
    return not all((target / "notes" / f"{n}.md").is_file() for n in prior["notes"])


def wiki_status(store, target: Path):
    """Backlog depth and last compile time, so `status` can show a stalled queue."""
    manifest = manifest_for(target)
    pending = sum(1 for row in store.items() if _awaiting_notes(row, manifest, target))
    last = None
    path = target / "last-compile.json"
    if path.is_file():
        try:
            value = json.loads(path.read_text())
            if isinstance(value, dict) and isinstance(value.get("at"), str):
                last = value["at"]
        except (OSError, ValueError):
            last = None
    return {"pending": pending, "last_compile": last}


def search_notes(target: Path, query: str, limit: int = 20) -> list[dict]:
    """Notes whose title or body contains the query.

    Only the text above the first section heading is searched. The trailing
    「来源」and「相关知识」sections hold source hashes and other notes' slugs,
    so searching them would return every note that merely links to a match.
    """
    directory = target / "notes"
    if not directory.is_dir():
        return []
    needle = query.casefold()
    titled, body_only = [], []
    for path in sorted(directory.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # Title and body only; the marker line is template, not content.
        front = text.split("\n## ", 1)[0].strip()
        if needle not in front.casefold():
            continue
        lines = front.split("\n")
        title = lines[0].lstrip("# ").strip()
        body = "\n".join(lines[1:]).strip()
        if body.startswith(NOTE_MARKER):
            body = body[len(NOTE_MARKER) :].strip()
        record = {"slug": path.stem, "title": title, "body": body}
        (titled if needle in title.casefold() else body_only).append(record)
    return (titled + body_only)[:limit]


def export_wiki(store, target: Path, engine: str):
    private_dir(target)
    taxonomy = load_taxonomy(target)
    # Wiki source pages sit one level deeper than report pages, and the media
    # directory lives beside the archive rather than inside the wiki tree.
    export_markdown(
        store,
        target / "sources",
        engine,
        media=local_media(target.parent),
        media_base="../../../media",
    )
    private_dir(target / "notes")
    private_dir(target / "personal")
    manifest = manifest_for(target)
    queue = []
    for row in store.items():
        if not _awaiting_notes(row, manifest, target):
            continue
        item = row["document"]
        key = source_key(item)
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
    grouped = defaultdict(list)
    for p in sorted((target / "notes").glob("*.md")):
        first = p.read_text().splitlines()
        label = first[0][2:] if first and first[0].startswith("# ") else safe_text(p.stem)
        meta_path = p.with_suffix(".meta.json")
        try:
            meta = classify(
                json.loads(meta_path.read_text()) if meta_path.exists() else {}, taxonomy
            )
        except (ValueError, OSError) as exc:
            raise AtlasError("知识笔记分类元数据损坏。") from exc
        for topic in meta["topics"]:
            grouped[topic].append(
                f"- [{label}](notes/{p.name}) · {safe_text(taxonomy['note_types'][meta['type']])}"
            )
    for topic, entries in grouped.items():
        index += [f"### {safe_text(taxonomy['topics'][topic])}", "", *entries, ""]
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
    taxonomy = load_taxonomy(target)
    rows = {source_key(r["document"]): r for r in store.items()}
    manifest = manifest_for(target)
    outputs, handled = {}, defaultdict(list)
    classifications = {}
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
        meta_path = target / "notes" / f"{slug}.meta.json"
        try:
            previous_meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            if not isinstance(previous_meta, dict):
                raise ValueError("object required")
        except (ValueError, OSError) as exc:
            raise AtlasError("已有知识笔记分类元数据损坏，请先修复。") from exc
        classifications[slug] = classify(previous_meta | note, taxonomy)
        refs = note.get("sources")
        if not isinstance(refs, list) or not refs:
            raise AtlasError("每条知识笔记必须附上来源。")
        lines = [
            f"# {safe_text(note['title'])}",
            "",
            NOTE_MARKER,
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
        if classifications[slug]["related"]:
            lines += ["", "## 相关知识", ""] + [
                f"- [{r}]({r}.md)" for r in classifications[slug]["related"]
            ]
        outputs[slug] = "\n".join(lines) + "\n"
    for key, slugs in handled.items():
        prior = manifest.get(key, {})
        if prior.get("hash") not in (None, rows[key]["content_hash"]) and not set(
            prior["notes"]
        ).issubset(slugs):
            raise AtlasError("原文已变化，请一并更新与该来源关联的全部知识笔记。")
    for slug, meta in classifications.items():
        for related in meta["related"]:
            if related == slug or (
                related not in outputs and not (target / "notes" / f"{related}.md").is_file()
            ):
                raise AtlasError("关联笔记不存在或指向自身。")
    private_dir(target / "notes")
    for slug, content in outputs.items():
        atomic_write(target / "notes" / f"{slug}.md", content)
        atomic_write(
            target / "notes" / f"{slug}.meta.json",
            json.dumps(classifications[slug], ensure_ascii=False, indent=2),
        )
    for key, slugs in handled.items():
        prior = manifest.get(key, {})
        if prior.get("hash") == rows[key]["content_hash"]:
            slugs = sorted(set(slugs + prior.get("notes", [])))
        manifest[key] = {"hash": rows[key]["content_hash"], "notes": slugs}
    atomic_write(target / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    receipt = {"at": now(), "notes": list(outputs), "sources": len(handled)}
    atomic_write(target / "last-compile.json", json.dumps(receipt, ensure_ascii=False, indent=2))
    log = target / "log.md"
    previous = log.read_text() if log.exists() else "# Wiki 更新记录\n"
    atomic_write(
        log,
        previous
        + f"\n## {receipt['at']} · 整理\n\n"
        + "\n".join(f"- [{slug}](notes/{slug}.md)" for slug in outputs)
        + "\n",
    )
    return receipt
