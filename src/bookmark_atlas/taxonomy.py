"""Small editable classification contract for the private Wiki."""

import json
import re

from .config import atomic_write
from .models import AtlasError

DEFAULT = {
    "topics": {
        "ai-agents": "AI 与 Agent",
        "development": "开发与开源",
        "research": "研究与论文",
        "product": "产品与商业",
        "design": "设计与创作",
        "learning": "学习与方法",
        "inbox": "待分类",
    },
    "note_types": {
        "concept": "概念",
        "method": "方法",
        "project": "项目",
        "entity": "人物与组织",
        "insight": "观察与判断",
        "reference": "资料索引",
    },
}


def load_taxonomy(target):
    path = target / "taxonomy.json"
    if not path.exists():
        atomic_write(path, json.dumps(DEFAULT, ensure_ascii=False, indent=2))
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or set(value) != {"topics", "note_types"}:
            raise ValueError()
        for mapping in value.values():
            if (
                not isinstance(mapping, dict)
                or not mapping
                or not all(
                    isinstance(k, str)
                    and re.fullmatch(r"[a-z][a-z0-9-]{0,39}", k)
                    and isinstance(v, str)
                    and 0 < len(v) <= 50
                    for k, v in mapping.items()
                )
            ):
                raise ValueError()
        if "inbox" not in value["topics"] or "insight" not in value["note_types"]:
            raise ValueError()
        return value
    except (OSError, ValueError, TypeError) as exc:
        raise AtlasError("Wiki taxonomy.json 无效；保留 inbox 和 insight 默认项。") from exc


def classify(note, taxonomy):
    topics = note.get("topics", ["inbox"])
    kind = note.get("type", "insight")
    related = note.get("related", [])
    if (
        not isinstance(topics, list)
        or not topics
        or not all(isinstance(t, str) and t in taxonomy["topics"] for t in topics)
    ):
        raise AtlasError("知识笔记主题不在 taxonomy.json 中。")
    if not isinstance(kind, str) or kind not in taxonomy["note_types"]:
        raise AtlasError("知识笔记类型不在 taxonomy.json 中。")
    if not isinstance(related, list) or not all(
        isinstance(r, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", r) for r in related
    ):
        raise AtlasError("关联笔记 ID 无效。")
    return {
        "topics": list(dict.fromkeys(topics)),
        "type": kind,
        "related": list(dict.fromkeys(related)),
    }
