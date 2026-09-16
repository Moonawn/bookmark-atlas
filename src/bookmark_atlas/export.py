from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from pathlib import Path

from .config import atomic_write, private_dir
from .models import digest, now


def safe_text(value: str) -> str:
    # Archived content stays readable without active HTML or hidden control characters.
    value = "".join(c for c in value if c in "\n\t" or ord(c) >= 32)
    return re.sub(r"([\\`*_{}\[\]()#!|])", r"\\\1", html.escape(value))


def export_json(store, target: Path):
    documents = store.items()
    atomic_write(
        target,
        json.dumps(
            {"schema_version": 1, "exported_at": now(), "items": documents},
            ensure_ascii=False,
            indent=2,
        ),
    )
    return {"items": len(documents), "path": str(target)}


def local_media(home: Path) -> dict:
    """Media already downloaded, keyed by source key.

    Absent or unreadable means "no local media": an export must still succeed
    when nothing has been fetched, and a damaged index is not its problem.
    """
    path = home / "media" / "index.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def media_lines(entry: dict, base: str = "../../media") -> list[str]:
    """Markdown for one item's media, pointing at the local copies.

    A video that was only framed keeps its original URL visible, so the
    reader knows the full file exists and where it lives.
    """
    lines = []
    for record in entry.get("files", []):
        if name := record.get("file"):
            lines += [f"![{record.get('kind', 'media')}]({base}/{name})", ""]
        elif name := record.get("thumbnail"):
            lines += [
                f"![视频截图]({base}/{name})",
                "",
                f"> 完整视频未下载：{record.get('original', '')}",
                "",
            ]
    return lines


def export_markdown(
    store,
    target: Path,
    engine: str,
    media: dict | None = None,
    media_base: str = "../../media",
):
    """Render items to Markdown.

    `media_base` is the path from `target/items/` back to the media directory;
    it differs between the report tree and the wiki source tree, so the caller
    says which depth it is writing at.
    """
    private_dir(target)
    if media is None:
        media = local_media(target.parent)
    topics = defaultdict(list)
    recent = []
    rows = store.items()
    for row in rows:
        item = row["document"]
        filename = f"{digest([item['site'], item['item_id']])[:24]}.md"
        analysis = store.db.execute(
            "SELECT result FROM analyses WHERE site=? AND item_id=? AND content_hash=? AND engine=?",
            (item["site"], item["item_id"], row["content_hash"], engine),
        ).fetchone()
        result = json.loads(analysis[0]) if analysis else {}
        label = safe_text(
            f"{item['author'] or item['site']} · {item['text'][:60].replace(chr(10), ' ')}"
        )
        reference = f"[{label}](items/{filename})"
        recent.append(reference)
        for topic in result.get("topics", ["待分析"]):
            topics[topic].append(reference)
        lines = [
            f"# {label}",
            "",
            f"原文：{safe_text(item['url'])}",
            f"作者：{safe_text(item['author'])}",
            f"推文发布时间：{safe_text(item['published_at']) or '未知'}",
            f"本地首次发现：{row['first_seen']}",
            "",
            "## 原文",
            "",
            *["> " + safe_text(line) for line in item["text"].splitlines()],
            "",
        ]
        if result:
            lines += [
                f"## {safe_text(result['summary_kind'])}",
                "",
                safe_text(result["summary"]),
                "",
                "主题：" + "、".join(safe_text(t) for t in result["topics"]),
                "",
            ]
            lines += ["- " + safe_text(point) for point in result.get("key_points", [])]
            lines += ["", "后续行动：" + safe_text(result.get("action", "")), ""]
        entry = media.get(digest([item["site"], item["item_id"]])[:24])
        if entry and (found := media_lines(entry, media_base)):
            # Point at the local copies rather than the remote originals, so a
            # deleted post still reads with its images.
            lines += ["## 本地媒体", "", *found]
        lines += ["## 关联链接", ""] + ["- " + safe_text(link) for link in item["links"]]
        atomic_write(target / "items" / filename, "\n".join(lines))
    index = [
        "# 收藏地图",
        "",
        f"共 {len(rows)} 条收藏；生成时间 {now()}。",
        "",
        "按主题连接相关收藏。规则模式提供摘录与分类；本地模型模式提供中文摘要。",
        "",
    ]
    for topic, references in sorted(topics.items()):
        index += (
            [f"## {safe_text(topic)}（{len(references)}）", ""]
            + ["- " + x for x in references]
            + [""]
        )
    atomic_write(target / "index.md", "\n".join(index))
    atomic_write(
        target / "recent.md",
        "\n".join(
            ["# 最近入库", "", "按本地首次发现时间排序，不以推文发布时间判断新增。", ""]
            + ["- " + x for x in recent[:100]]
        ),
    )
    return {"items": len(rows), "topics": len(topics), "path": str(target / "index.md")}
