from __future__ import annotations

import fcntl
import time
from contextlib import contextmanager
from pathlib import Path

from .config import private_dir
from .models import AtlasError, AuthError, ParseError, RateLimitError, UnavailableError
from .store import Store


class Prefetched:
    def __init__(self, adapter, identity, first_page):
        self.adapter, self._identity, self.first_page = adapter, identity, first_page
        self.name = adapter.name

    def identity(self):
        return self._identity

    def page(self, cursor=None):
        return self.first_page if cursor is None else self.adapter.page(cursor)


@contextmanager
def process_lock(home: Path):
    private_dir(home)
    with (home / "archive.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AtlasError("已有同步或分析任务运行，请稍后重试。") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def sync_one(
    store: Store, adapter, *, max_pages=50, full=False, overlap_pages=2, delay=2, sleep=time.sleep
) -> dict:
    if max_pages < 1 or overlap_pages < 1 or delay < 0:
        raise ValueError("分页与间隔参数无效")
    identity = adapter.identity()
    store.bind_owner(identity)
    if store.cooldown(identity.site) > time.time():
        raise RateLimitError(store.cooldown(identity.site))
    known = store.known(identity)
    saved = None if full else store.checkpoint(identity.site, adapter.name)
    run_id = store.start(identity.site, adapter.name)
    cursor = None
    seen_cursors = set()
    known_streak = 0
    phase = "head"
    try:
        for page_index in range(max_pages):
            if page_index:
                sleep(delay)
            marker = (phase, cursor)
            if marker in seen_cursors:
                raise ParseError("接口分页游标重复，已停止；归档数据保留。")
            seen_cursors.add(marker)
            page = adapter.page(cursor)
            # Entire known pages, not one duplicate item, define overlap.
            all_known = bool(page.items) and all(item.item_id in known for item in page.items)
            known_streak = known_streak + 1 if all_known else 0
            exhausted = not page.next_cursor or (not page.items and page.next_cursor == cursor)
            overlap = phase == "head" and not full and known_streak >= overlap_pages
            if not exhausted and page.next_cursor == cursor and cursor is not None:
                raise ParseError("接口返回了相同游标，未推进检查点。")
            # While refreshing the head, preserve an unfinished older backfill.
            checkpoint = saved if phase == "head" and saved else page.next_cursor
            if exhausted:
                checkpoint = None  # Reached the end without needing the old cursor.
            elif overlap:
                checkpoint = saved
            store.commit_page(identity, adapter.name, run_id, page, cursor, checkpoint)
            if exhausted:
                store.finish(run_id, "complete")
                return store.run(run_id)
            if overlap:
                if saved:
                    phase, cursor = "backfill", saved
                    saved = None
                    known_streak = 0
                    continue
                store.finish(run_id, "incremental")
                return store.run(run_id)
            cursor = page.next_cursor
        store.finish(run_id, "partial")
        return store.run(run_id)
    except RateLimitError as exc:
        store.set_cooldown(identity.site, exc.retry_at)
        store.finish(run_id, "rate_limited", str(exc))
        raise
    except Exception as exc:
        store.finish(
            run_id, "failed", str(exc) if isinstance(exc, AtlasError) else type(exc).__name__
        )
        raise
    except BaseException:
        store.finish(run_id, "interrupted", "Process interrupted; committed pages preserved")
        raise


def sync(store: Store, factories: dict, *, site="x", mode="auto", preferred="api", **kwargs):
    # A site-wide cooldown applies to both transports, including identity calls.
    if store.cooldown(site) > time.time():
        raise RateLimitError(store.cooldown(site))
    order = (
        [mode]
        if mode != "auto"
        else [preferred] + [name for name in factories if name != preferred]
    )
    fallbacks = []
    for index, name in enumerate(order):
        adapter = None
        try:
            adapter = factories[name]()
            identity = adapter.identity()
            store.bind_owner(identity)
            # Verify collection access before committing any pages. Some API accounts
            # can read users/me but do not have bookmark.read or usable API credits.
            first_page = adapter.page(None)
        except RateLimitError as exc:
            store.set_cooldown(site, exc.retry_at)
            if adapter:
                adapter.close()
            raise
        except (AuthError, UnavailableError) as exc:
            if adapter:
                adapter.close()
            if index + 1 == len(order):
                raise
            fallbacks.append({"adapter": name, "reason": str(exc)})
            continue
        except BaseException:
            if adapter:
                adapter.close()
            raise
        try:
            result = sync_one(store, Prefetched(adapter, identity, first_page), **kwargs)
            result["fallbacks"] = fallbacks
            return result
        finally:
            adapter.close()
    raise AtlasError("没有可用的站点适配器。")
