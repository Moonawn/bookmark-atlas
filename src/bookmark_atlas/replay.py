"""Re-parse archived raw responses with the current parser.

Captures are the only copy of what the site actually returned. When the parser
learns to read a field it previously skipped, replaying those stored responses
recovers the data without contacting the site again — which matters because
posts can be deleted between now and when the parser improves.

Replay never fetches anything. It reads captures already on disk, and touches
only the `items` table: memberships, origins, checkpoints and the captures
themselves are left alone.
"""

from __future__ import annotations

import json
from typing import Any

from .adapters.x_web import parse_page
from .models import Page, ParseError, now
from .store import merge_document, updated_content_hash
from .wechat import parse_article


def replay(
    store,
    *,
    since: str | None = None,
    until: str | None = None,
    apply: bool = False,
    sample: int = 10,
) -> dict[str, Any]:
    """Re-parse stored captures and report what the current parser yields.

    Without `apply` this is read-only and reports differences. With `apply`,
    differing items are merged back using the same conservative rules as sync:
    a shorter text never replaces a longer one, and media entries merge by key.
    """
    clauses, params = [], []
    if since:
        clauses.append("captured_at >= ?")
        params.append(since)
    if until:
        clauses.append("captured_at <= ?")
        params.append(until)
    query = "SELECT id,captured_at,payload FROM captures"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id"
    rows = list(store.db.execute(query, params))

    parsed: dict[str, Any] = {}
    parse_failures = 0
    for row in rows:
        try:
            payload = json.loads(row["payload"])
            page = (
                Page([parse_article(payload["html"], payload["url"])], None, payload)
                if "html" in payload and "url" in payload
                else parse_page(payload)
            )
        except ParseError:
            # A page the current parser cannot read is reported, not fatal:
            # older captures may predate a structural change.
            parse_failures += 1
            continue
        for item in page.items:
            parsed[(item.site, item.item_id)] = item

    known = unknown = 0
    changed: list[tuple[Any, dict, str]] = []
    for (_, item_id), item in parsed.items():
        old = store.db.execute(
            "SELECT document,content_hash FROM items WHERE site=? AND item_id=?",
            (item.site, item_id),
        ).fetchone()
        if not old:
            # The parser now reads an item the archive never stored. Replay
            # does not create memberships, so it reports rather than inserts.
            unknown += 1
            continue
        known += 1
        previous = json.loads(old["document"])
        merged = merge_document(previous, item.to_dict())
        if merged != previous:
            changed.append(
                (item, merged, updated_content_hash(previous, merged, old["content_hash"]))
            )

    result: dict[str, Any] = {
        "captures": len(rows),
        "parsed": len(parsed),
        "known": known,
        "unknown": unknown,
        "changed": len(changed),
        "parse_failures": parse_failures,
        "applied": False,
        "sample": [item.item_id for item, _, _ in changed[:sample]],
    }
    if not apply or not changed:
        return result

    stamp = now()
    with store.db:
        for item, merged, hashed in changed:
            store.db.execute(
                "UPDATE items SET document=?,content_hash=?,last_seen=? WHERE site=? AND item_id=?",
                (
                    json.dumps(merged, ensure_ascii=False),
                    hashed,
                    stamp,
                    item.site,
                    item.item_id,
                ),
            )
    result["applied"] = True
    return result
