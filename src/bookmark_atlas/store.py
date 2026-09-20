from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .config import private_dir
from .models import Identity, OwnerMismatch, Page, digest, now, stable_media_url

SCHEMA = """
CREATE TABLE IF NOT EXISTS owners(site TEXT PRIMARY KEY, account_id TEXT NOT NULL, username TEXT);
CREATE TABLE IF NOT EXISTS items(
 site TEXT, item_id TEXT, document TEXT NOT NULL, content_hash TEXT NOT NULL,
 first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, PRIMARY KEY(site,item_id));
CREATE TABLE IF NOT EXISTS memberships(
 site TEXT, account_id TEXT, item_id TEXT, first_seen TEXT, last_seen TEXT,
 PRIMARY KEY(site,account_id,item_id));
CREATE TABLE IF NOT EXISTS runs(
 id INTEGER PRIMARY KEY, site TEXT, adapter TEXT, started_at TEXT, finished_at TEXT,
 status TEXT, pages INTEGER DEFAULT 0, added INTEGER DEFAULT 0, updated INTEGER DEFAULT 0, error TEXT);
CREATE TABLE IF NOT EXISTS captures(
 id INTEGER PRIMARY KEY, run_id INTEGER, captured_at TEXT, cursor TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS origins(
 site TEXT, item_id TEXT, adapter TEXT, last_seen TEXT, PRIMARY KEY(site,item_id,adapter));
CREATE TABLE IF NOT EXISTS checkpoints(
 site TEXT, adapter TEXT, cursor TEXT, PRIMARY KEY(site,adapter));
CREATE TABLE IF NOT EXISTS cooldowns(site TEXT PRIMARY KEY, retry_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS analyses(
 site TEXT, item_id TEXT, content_hash TEXT, engine TEXT, result TEXT, created_at TEXT,
 PRIMARY KEY(site,item_id,engine));
CREATE TABLE IF NOT EXISTS analysis_failures(
 site TEXT, item_id TEXT, engine TEXT, error TEXT, attempted_at TEXT,
 PRIMARY KEY(site,item_id,engine));
CREATE TABLE IF NOT EXISTS changes(
 id INTEGER PRIMARY KEY, run_id INTEGER, site TEXT, item_id TEXT, kind TEXT, content_hash TEXT);
CREATE TABLE IF NOT EXISTS collection_memberships(
 site TEXT, account_id TEXT, collection TEXT, item_id TEXT, first_seen TEXT, last_seen TEXT,
 PRIMARY KEY(site,account_id,collection,item_id));
INSERT OR IGNORE INTO collection_memberships
 SELECT site,account_id,'bookmarks',item_id,first_seen,last_seen FROM memberships;
PRAGMA user_version=2;
"""


def merge_document(old: dict, incoming: dict) -> dict:
    merged = dict(old)
    for key, value in incoming.items():
        if key == "media":
            media = {}
            for entry in old.get(key, []) + value:
                identity = (
                    entry.get("media_key")
                    or entry.get("id_str")
                    or stable_media_url(entry.get("url") or entry.get("media_url_https") or "")
                    or digest(entry)
                )
                media[identity] = {**media.get(identity, {}), **entry}
            merged[key] = list(media.values())
        elif key == "links":
            merged[key] = list(
                {json.dumps(x, sort_keys=True): x for x in old.get(key, []) + value}.values()
            )
        elif key == "text":
            # A shorter API preview must not erase a captured long post.
            if len(value) >= len(old.get(key, "")):
                merged[key] = value
        elif value:
            merged[key] = value
    return merged


def content_digest(document: dict) -> str:
    # Source metadata such as video views/owner counters changes independently of
    # the saved content. Preserve it, but do not trigger a new analysis for it.
    stable = dict(document)
    stable.pop("kind", None)
    stable.pop("language", None)

    def normalize(value):
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [normalize(v) for v in value]
        if isinstance(value, str) and value.startswith("https://"):
            return stable_media_url(value)
        return value

    stable["media"] = normalize(
        [
            {
                key: value
                for key, value in entry.items()
                if key not in ("additional_media_info", "mediaStats", "ext", "indices")
            }
            for entry in document.get("media", [])
        ]
    )
    return digest(stable)


def updated_content_hash(previous: dict, incoming: dict, prior_hash: str) -> str:
    """Preserve existing Wiki/analysis receipts across a hash-policy upgrade."""
    current = content_digest(incoming)
    return prior_hash if content_digest(previous) == current else current


class Store:
    def __init__(self, home: Path):
        private_dir(home)
        self.path = home / "atlas.sqlite3"
        self.db = sqlite3.connect(self.path)
        self.path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 2:
            self.db.close()
            raise RuntimeError("数据库版本高于当前程序，请升级 Bookmark Atlas。")
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def bind_owner(self, identity: Identity):
        row = self.db.execute(
            "SELECT account_id FROM owners WHERE site=?", (identity.site,)
        ).fetchone()
        if row and row[0] != identity.account_id:
            raise OwnerMismatch("两个入口的账号不一致；请切换登录，或为另一个账号使用独立 --home。")
        with self.db:
            self.db.execute(
                "INSERT INTO owners VALUES(?,?,?) ON CONFLICT(site) DO UPDATE SET username=excluded.username",
                (identity.site, identity.account_id, identity.username),
            )

    def owner(self, site="x"):
        row = self.db.execute("SELECT * FROM owners WHERE site=?", (site,)).fetchone()
        return dict(row) if row else None

    def start(self, site: str, adapter: str) -> int:
        with self.db:
            return self.db.execute(
                "INSERT INTO runs(site,adapter,started_at,status) VALUES(?,?,?,'running')",
                (site, adapter, now()),
            ).lastrowid

    def finish(self, run_id: int, status: str, error: str | None = None):
        with self.db:
            self.db.execute(
                "UPDATE runs SET status=?,finished_at=?,error=? WHERE id=?",
                (status, now(), error, run_id),
            )

    def checkpoint(self, site: str, adapter: str) -> str | None:
        row = self.db.execute(
            "SELECT cursor FROM checkpoints WHERE site=? AND adapter=?", (site, adapter)
        ).fetchone()
        return row[0] if row else None

    def known(self, identity: Identity, collection="bookmarks") -> set[str]:
        return {
            r[0]
            for r in self.db.execute(
                "SELECT item_id FROM collection_memberships WHERE site=? AND account_id=? AND collection=?",
                (identity.site, identity.account_id, collection),
            )
        }

    def commit_page(
        self,
        identity: Identity,
        adapter: str,
        run_id: int,
        page: Page,
        cursor: str | None,
        checkpoint: str | None,
        collection: str = "bookmarks",
    ) -> tuple[int, int]:
        added = updated = 0
        stamp = now()
        with self.db:
            self.db.execute(
                "INSERT INTO captures(run_id,captured_at,cursor,payload) VALUES(?,?,?,?)",
                (run_id, stamp, cursor, json.dumps(page.raw, ensure_ascii=False)),
            )
            for item in page.items:
                if item.site != identity.site:
                    raise ValueError("Adapter emitted an item for a different site")
                old = self.db.execute(
                    "SELECT document,content_hash FROM items WHERE site=? AND item_id=?",
                    (item.site, item.item_id),
                ).fetchone()
                document = (
                    merge_document(json.loads(old["document"]), item.to_dict())
                    if old
                    else item.to_dict()
                )
                hashed = content_digest(document)
                if old:
                    hashed = updated_content_hash(
                        json.loads(old["document"]), document, old["content_hash"]
                    )
                member = self.db.execute(
                    "SELECT 1 FROM collection_memberships WHERE site=? AND account_id=? AND collection=? AND item_id=?",
                    (identity.site, identity.account_id, collection, item.item_id),
                ).fetchone()
                kind = "new" if not member else "updated" if old["content_hash"] != hashed else None
                added += kind == "new"
                updated += kind == "updated"
                self.db.execute(
                    "INSERT INTO items VALUES(?,?,?,?,?,?) ON CONFLICT(site,item_id) DO UPDATE SET document=excluded.document,content_hash=excluded.content_hash,last_seen=excluded.last_seen",
                    (
                        item.site,
                        item.item_id,
                        json.dumps(document, ensure_ascii=False),
                        hashed,
                        stamp,
                        stamp,
                    ),
                )
                self.db.execute(
                    "INSERT INTO collection_memberships VALUES(?,?,?,?,?,?) ON CONFLICT(site,account_id,collection,item_id) DO UPDATE SET last_seen=excluded.last_seen",
                    (identity.site, identity.account_id, collection, item.item_id, stamp, stamp),
                )
                if collection == "bookmarks":
                    self.db.execute(
                        "INSERT INTO memberships VALUES(?,?,?,?,?) ON CONFLICT(site,account_id,item_id) DO UPDATE SET last_seen=excluded.last_seen",
                        (identity.site, identity.account_id, item.item_id, stamp, stamp),
                    )
                self.db.execute(
                    "INSERT INTO origins VALUES(?,?,?,?) ON CONFLICT(site,item_id,adapter) DO UPDATE SET last_seen=excluded.last_seen",
                    (item.site, item.item_id, adapter, stamp),
                )
                if kind:
                    self.db.execute(
                        "INSERT INTO changes(run_id,site,item_id,kind,content_hash) VALUES(?,?,?,?,?)",
                        (run_id, item.site, item.item_id, kind, hashed),
                    )
            self.db.execute(
                "INSERT INTO checkpoints VALUES(?,?,?) ON CONFLICT(site,adapter) DO UPDATE SET cursor=excluded.cursor",
                (identity.site, adapter, checkpoint),
            )
            self.db.execute(
                "UPDATE runs SET pages=pages+1,added=added+?,updated=updated+? WHERE id=?",
                (added, updated, run_id),
            )
        return added, updated

    def cooldown(self, site: str) -> float:
        row = self.db.execute("SELECT retry_at FROM cooldowns WHERE site=?", (site,)).fetchone()
        return row[0] if row else 0

    def set_cooldown(self, site: str, retry_at: float):
        with self.db:
            self.db.execute(
                "INSERT INTO cooldowns VALUES(?,?) ON CONFLICT(site) DO UPDATE SET retry_at=MAX(retry_at,excluded.retry_at)",
                (site, retry_at),
            )

    def run(self, run_id: int) -> dict:
        return dict(self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())

    def items(self, search: str = "", limit: int | None = None) -> list[dict]:
        sql = "SELECT * FROM items ORDER BY first_seen DESC,item_id DESC"
        rows = []
        for row in self.db.execute(sql):
            value = dict(row)
            value["document"] = json.loads(value["document"])
            if (
                search
                and search.casefold()
                not in json.dumps(value["document"], ensure_ascii=False).casefold()
            ):
                continue
            value["collections"] = [
                r[0]
                for r in self.db.execute(
                    "SELECT DISTINCT collection FROM collection_memberships WHERE site=? AND item_id=? ORDER BY collection",
                    (value["site"], value["item_id"]),
                )
            ]
            rows.append(value)
            if limit and len(rows) >= limit:
                break
        return rows

    def pending(self, engine: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT i.* FROM items i LEFT JOIN analyses a ON i.site=a.site AND i.item_id=a.item_id AND a.engine=? WHERE a.content_hash IS NULL OR a.content_hash<>i.content_hash",
            (engine,),
        )
        return [{**dict(r), "document": json.loads(r["document"])} for r in rows]

    def save_analysis(self, row: dict, engine: str, result: dict):
        with self.db:
            self.db.execute(
                "INSERT INTO analyses VALUES(?,?,?,?,?,?) ON CONFLICT(site,item_id,engine) DO UPDATE SET content_hash=excluded.content_hash,result=excluded.result,created_at=excluded.created_at",
                (
                    row["site"],
                    row["item_id"],
                    row["content_hash"],
                    engine,
                    json.dumps(result, ensure_ascii=False),
                    now(),
                ),
            )
            self.db.execute(
                "DELETE FROM analysis_failures WHERE site=? AND item_id=? AND engine=?",
                (row["site"], row["item_id"], engine),
            )

    def stats(self) -> dict:
        return {
            "items": self.db.execute("SELECT COUNT(*) FROM items").fetchone()[0],
            "analyses": self.db.execute("SELECT COUNT(*) FROM analyses").fetchone()[0],
            "owners": [dict(r) for r in self.db.execute("SELECT * FROM owners")],
            "last_run": dict(r)
            if (r := self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone())
            else None,
            "checkpoints": [
                dict(r)
                for r in self.db.execute("SELECT * FROM checkpoints WHERE cursor IS NOT NULL")
            ],
        }
