from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import __version__
from .adapters.x_api import XAPI
from .adapters.x_web import XWeb
from .analysis import LocalAnalysis, OllamaAnalysis, analyze_pending
from .config import home_path
from .export import export_json, export_markdown
from .media import fetch_media
from .models import AtlasError, RateLimitError
from .oauth import login
from .preferences import load, next_daily, setup, validate
from .replay import replay
from .store import Store
from .sync import process_lock, sync
from .watch import read_rules, run_watches, save_rule
from .wiki import apply_notes, export_wiki, wiki_status


def positive(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("必须是正整数")
    return result


def parser():
    root = argparse.ArgumentParser(description="Bookmark Atlas · 本地收藏归档与整理")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--home", help="私有数据目录（默认 ~/.local/share/bookmark-atlas）")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("sync", "serve"):
        cmd = commands.add_parser(
            name, help="同步收藏" if name == "sync" else "按间隔执行同步和整理"
        )
        cmd.add_argument("--site", choices=["x"], default="x")
        cmd.add_argument("--mode", choices=["auto", "api", "web"], default=None)
        cmd.add_argument("--prefer", choices=["api", "web"], default="api")
        cmd.add_argument(
            "--browser",
            choices=["chrome", "firefox", "brave", "edge", "chromium", "quark"],
            default=None,
        )
        cmd.add_argument("--cookie-file", help="指定浏览器配置的 Cookies 数据库路径")
        cmd.add_argument("--max-pages", type=positive, default=50)
        cmd.add_argument(
            "--full", action="store_true", help="从头遍历，不按重复页提前结束；不删除旧数据"
        )
        cmd.add_argument("--overlap-pages", type=positive, default=2)
        cmd.add_argument("--no-analyze", action="store_true")
        cmd.add_argument("--organization", choices=["topics", "wiki"])
        cmd.add_argument("--ollama-model", help="使用已安装的本地 Ollama 模型生成中文摘要")
        if name == "serve":
            cmd.add_argument("--at", help="每日运行时间 HH:MM")
            cmd.add_argument("--timezone", help="IANA 时区，默认 Asia/Shanghai")
            cmd.add_argument(
                "--interval",
                type=positive,
                default=None,
                help="同步间隔秒数，默认 6 小时，最少 60 秒",
            )
            cmd.add_argument(
                "--once", action="store_true", help="执行一轮后退出，用于系统调度和验证"
            )
    watch = commands.add_parser("watch", help="配置作者与关注列表的定向采集").add_subparsers(
        dest="watch_command", required=True
    )
    watch.add_parser("list")
    add = watch.add_parser("add")
    add.add_argument("name")
    add.add_argument("--authors", default="", help="逗号分隔，不带 @")
    add.add_argument("--following", action="store_true")
    add.add_argument("--keywords", default="", help="逗号分隔，任一词命中即可")
    add.add_argument("--days", type=positive, default=7)
    add.add_argument("--kinds", default="post,quote")
    add.add_argument("--language", default="")
    add.add_argument("--max-authors", type=positive, default=20)
    add.add_argument("--max-pages", type=positive, default=5)
    for action in ("enable", "disable"):
        toggle = watch.add_parser(action)
        toggle.add_argument("name")
    collect = watch.add_parser("sync")
    collect.add_argument("name", nargs="?")
    collect.add_argument("--mode", choices=["auto", "api", "web"])
    collect.add_argument("--prefer", choices=["api", "web"], default="api")
    collect.add_argument("--browser")
    collect.add_argument("--cookie-file")
    cmd = commands.add_parser("analyze", help="分析新增或变更内容，失败可重试")
    cmd.add_argument("--ollama-model")
    cmd.add_argument("--limit", type=positive, default=100)
    cmd = commands.add_parser("export", help="导出 JSON 或 Markdown 主题知识库")
    cmd.add_argument("format", choices=["json", "markdown", "wiki"])
    cmd.add_argument("--out", type=Path)
    cmd.add_argument("--ollama-model")
    commands.add_parser("setup", help="询问并保存同步时间和整理偏好")
    commands.add_parser("settings", help="查看已保存的使用偏好")
    wiki = commands.add_parser("wiki", help="接收 Agent 生成的带来源知识笔记")
    wiki.add_argument("action", choices=["apply"])
    wiki.add_argument("input", type=Path)
    commands.add_parser("status", help="查看本地归档与同步状态")
    cmd = commands.add_parser("fetch-media", help="下载收藏引用的图片视频（独立于同步）")
    cmd.add_argument("--limit", type=positive, help="本次最多处理多少条收藏")
    cmd.add_argument("--dry-run", action="store_true", help="只列出待下载项，不写盘")
    cmd = commands.add_parser("replay", help="用当前解析器重新解析已存的原始响应")
    cmd.add_argument("--since", help="只重放该时间之后捕获的响应（ISO 时间）")
    cmd.add_argument("--until", help="只重放该时间之前捕获的响应（ISO 时间）")
    cmd.add_argument("--apply", action="store_true", help="把差异合并回库；默认只报告，不写入")
    cmd = commands.add_parser("search", help="搜索本地原文、作者和链接（支持中文子串）")
    cmd.add_argument("query")
    cmd.add_argument("--limit", type=positive, default=20)
    auth = commands.add_parser("auth", help="身份检查和官方 OAuth 登录").add_subparsers(
        dest="auth_command", required=True
    )
    check = auth.add_parser("check")
    check.add_argument("--mode", choices=["api", "web"], required=True)
    check.add_argument("--browser", default="chrome")
    check.add_argument("--cookie-file")
    oauth = auth.add_parser("login")
    oauth.add_argument("--client-id", default=os.getenv("ATLAS_X_CLIENT_ID"))
    oauth.add_argument("--port", type=int, default=8765)
    return root


def engine_for(args):
    model = getattr(args, "ollama_model", None)
    return OllamaAnalysis(model) if model else LocalAnalysis()


def factories_for(args, home):
    return {"api": lambda: XAPI(home), "web": lambda: XWeb(args.browser, args.cookie_file)}


def cycle(args, home):
    with process_lock(home):
        store = Store(home)
        try:
            result = sync(
                store,
                factories_for(args, home),
                site=args.site,
                mode=args.mode,
                preferred=args.prefer,
                max_pages=args.max_pages,
                full=args.full,
                overlap_pages=args.overlap_pages,
            )
            try:
                result["watch"] = run_watches(
                    store, home, factories_for(args, home), mode=args.mode, preferred=args.prefer
                )
            except AtlasError as exc:
                result["watch"] = {"failed": True, "error": str(exc)}
            if not args.no_analyze:
                engine = engine_for(args)
                result["analysis"] = analyze_pending(store, engine, limit=500)
                # Report updates are recoverable by re-running export.
                result["report"] = export_markdown(store, home / "reports", engine.name)
                if args.organization == "wiki":
                    result["wiki"] = export_wiki(store, home / "wiki", engine.name)
            return result
        finally:
            store.close()


def run(args):
    home = home_path(args.home)
    if args.command == "setup":
        with process_lock(home):
            return setup(home)
    settings = load(home)
    if args.command == "settings":
        return settings
    for key in ("mode", "browser", "organization", "ollama_model"):
        if hasattr(args, key) and getattr(args, key) is None:
            setattr(args, key, settings[key])
    if args.command == "auth":
        if args.auth_command == "login":
            if not args.client_id:
                raise AtlasError("请提供 --client-id 或 ATLAS_X_CLIENT_ID。")
            with process_lock(home):
                login(home, args.client_id, args.port, os.getenv("ATLAS_X_CLIENT_SECRET"))
            return {"authorized": True}
        adapter = factories_for(args, home)[args.mode]()
        try:
            identity = adapter.identity()
            return {
                "site": identity.site,
                "adapter": args.mode,
                "account_id": identity.account_id,
                "username": identity.username,
            }
        finally:
            adapter.close()
    if args.command == "watch":
        with process_lock(home):
            rules = read_rules(home)
            if args.watch_command == "list":
                return rules
            if args.watch_command == "add":
                values = {
                    k: getattr(args, k)
                    for k in ("following", "days", "language", "max_authors", "max_pages")
                }
                for key in ("authors", "keywords", "kinds"):
                    values[key] = [
                        v.strip().lstrip("@") if key == "authors" else v.strip()
                        for v in getattr(args, key).split(",")
                        if v.strip()
                    ]
                return save_rule(home, args.name, values)
            if args.watch_command in ("enable", "disable"):
                if args.name not in rules:
                    raise AtlasError("规则不存在。")
                return save_rule(
                    home, args.name, rules[args.name] | {"enabled": args.watch_command == "enable"}
                )
            store = Store(home)
            try:
                result = run_watches(
                    store,
                    home,
                    factories_for(args, home),
                    mode=args.mode,
                    preferred=args.prefer,
                    name=args.name,
                )
                engine = (
                    OllamaAnalysis(settings["ollama_model"])
                    if settings["ollama_model"]
                    else LocalAnalysis()
                )
                result["analysis"] = analyze_pending(store, engine, limit=500)
                result["report"] = export_markdown(store, home / "reports", engine.name)
                if settings["organization"] == "wiki":
                    result["wiki"] = export_wiki(store, home / "wiki", engine.name)
                return result
            finally:
                store.close()
    if args.command == "sync":
        return cycle(args, home)
    if args.command == "serve":
        explicit_interval = args.interval is not None
        args.interval = args.interval or settings["interval"]
        daily = args.at or (
            settings["at"] if settings["schedule"] == "daily" and not explicit_interval else None
        )
        timezone = args.timezone or settings["timezone"]
        if daily:
            validate(settings | {"at": daily, "timezone": timezone})
        if args.interval < 60:
            raise AtlasError("同步间隔至少为 60 秒；建议从 6 小时开始。")
        retry_after = 0
        while True:
            if daily and not args.once:
                target = max(next_daily(daily, timezone, time.time()), retry_after)
                while (remaining := target - time.time()) > 0:
                    time.sleep(min(remaining, 30))
            try:
                result = cycle(args, home)
                if args.once:
                    return result
                if (
                    result["added"]
                    or result["updated"]
                    or result.get("analysis", {}).get("failed")
                    or any(result.get("watch", {}).get(k) for k in ("added", "updated", "failed"))
                ):
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                wait = args.interval
            except AtlasError as exc:
                if args.once:
                    raise
                print(
                    json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr, flush=True
                )
                wait = (
                    max(args.interval, exc.retry_at - time.time())
                    if isinstance(exc, RateLimitError)
                    else args.interval
                )
            if daily:
                retry_after = time.time() + wait
            else:
                time.sleep(wait)
    with process_lock(home):
        store = Store(home)
        try:
            if args.command == "status":
                result = store.stats()
                wiki_home = home / "wiki"
                if wiki_home.is_dir():
                    result["wiki"] = wiki_status(store, wiki_home)
                return result
            if args.command == "fetch-media":
                return fetch_media(store, home, limit=args.limit, dry_run=args.dry_run)
            if args.command == "replay":
                return replay(store, since=args.since, until=args.until, apply=args.apply)
            if args.command == "search":
                return [r["document"] for r in store.items(args.query, args.limit)]
            if args.command == "wiki":
                try:
                    payload = json.loads(args.input.expanduser().read_text())
                except (OSError, ValueError) as exc:
                    raise AtlasError("无法读取 Wiki 输入 JSON。") from exc
                receipt = apply_notes(store, home / "wiki", payload)
                receipt["wiki"] = export_wiki(
                    store,
                    home / "wiki",
                    settings["ollama_model"]
                    and f"ollama-v1:{settings['ollama_model']}"
                    or LocalAnalysis.name,
                )
                return receipt
            engine = engine_for(args)
            if args.command == "analyze":
                return analyze_pending(store, engine, args.limit)
            if args.command == "export":
                target = (
                    (
                        args.out
                        or home
                        / (
                            {
                                "json": "exports/bookmarks.json",
                                "markdown": "reports",
                                "wiki": "wiki",
                            }[args.format]
                        )
                    )
                    .expanduser()
                    .resolve()
                )
                return (
                    export_json(store, target)
                    if args.format == "json"
                    else (
                        export_wiki(store, target, engine.name)
                        if args.format == "wiki"
                        else export_markdown(store, target, engine.name)
                    )
                )
        finally:
            store.close()


def main():
    # Database sidecars and private exports inherit restrictive permissions.
    os.umask(0o077)
    try:
        result = run(parser().parse_args())
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        if isinstance(result, dict) and (
            result.get("failed")
            or result.get("analysis", {}).get("failed")
            or result.get("watch", {}).get("failed")
        ):
            return 1
        return 0
    except AtlasError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2 if isinstance(exc, RateLimitError) else 1
    except KeyboardInterrupt:
        print("已停止；已提交的数据和续传位置保留。", file=sys.stderr)
        return 130
    except Exception as exc:
        # No raw exception text: dependencies may embed headers or response bodies.
        print(
            json.dumps(
                {"error": "运行失败，请检查配置、文件权限或升级程序。", "type": type(exc).__name__},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
