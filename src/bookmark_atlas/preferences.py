"""Persistent onboarding choices; saving a schedule never installs a daemon."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import atomic_write
from .models import AtlasError

DEFAULTS = dict(
    mode="auto",
    browser="chrome",
    schedule="manual",
    interval=21600,
    at="21:00",
    timezone="Asia/Shanghai",
    organization="topics",
    summary="rules",
    ollama_model=None,
    media="none",
)
CHOICES = {
    "media": ("none", "images"),
    "mode": ("auto", "web", "api"),
    "browser": ("chrome", "firefox", "brave", "edge", "chromium", "quark"),
    "schedule": ("manual", "daily", "interval"),
    "organization": ("topics", "wiki"),
    "summary": ("rules", "agent", "ollama"),
}


def validate(values):
    if not isinstance(values, dict) or values.keys() - DEFAULTS.keys():
        raise AtlasError("settings.json 含有未知配置项。")
    settings = DEFAULTS | values
    for key, options in CHOICES.items():
        if settings[key] not in options:
            raise AtlasError(f"配置项 {key} 无效。")
    if type(settings["interval"]) is not int or settings["interval"] < 60:
        raise AtlasError("同步间隔至少为 60 秒。")
    if not isinstance(settings["at"], str) or not re.fullmatch(
        r"(?:[01]\d|2[0-3]):[0-5]\d", settings["at"]
    ):
        raise AtlasError("每日时间应为 HH:MM，例如 21:00。")
    try:
        ZoneInfo(settings["timezone"])
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise AtlasError("请使用有效的 IANA 时区，例如 Asia/Shanghai。") from exc
    model = settings["ollama_model"]
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise AtlasError("Ollama 模型名称无效。")
    if settings["summary"] == "ollama" and not model:
        raise AtlasError("Ollama 总结需要指定已安装的模型。")
    if settings["summary"] == "agent" and settings["organization"] != "wiki":
        raise AtlasError("Agent 整理需要选择 wiki 模式。")
    return settings


def load(home):
    path = home / "settings.json"
    if not path.exists():
        return dict(DEFAULTS)
    try:
        return validate(json.loads(path.read_text()))
    except (OSError, ValueError) as exc:
        raise AtlasError("无法读取 settings.json，请检查格式与权限。") from exc


def setup(home, ask=None):
    ask = ask or input
    settings = load(home)
    prompts = [
        ("mode", "采集入口 auto / web / api"),
        ("browser", "已登录 X 的浏览器"),
        ("schedule", "同步方式 manual / daily / interval"),
    ]
    try:
        for key, label in prompts:
            settings[key] = ask(f"{label} [{settings[key]}]: ").strip() or settings[key]
        if settings["schedule"] == "daily":
            for key, label in [("at", "每天几点 HH:MM"), ("timezone", "时区")]:
                settings[key] = ask(f"{label} [{settings[key]}]: ").strip() or settings[key]
        elif settings["schedule"] == "interval":
            settings["interval"] = int(
                ask(f"间隔秒数 [{settings['interval']}]: ").strip() or settings["interval"]
            )
        settings["organization"] = (
            ask(f"整理方式 topics 主题归档 / wiki 知识库 [{settings['organization']}]: ").strip()
            or settings["organization"]
        )
        settings["summary"] = (
            ask(
                f"总结方式 rules 原文摘录 / agent 当前 Agent / ollama 本地模型 [{settings['summary']}]: "
            ).strip()
            or settings["summary"]
        )
        if settings["summary"] == "ollama":
            settings["ollama_model"] = (
                ask(f"已安装的模型 [{settings['ollama_model'] or ''}]: ").strip()
                or settings["ollama_model"]
            )
        else:
            settings["ollama_model"] = None
    except (EOFError, ValueError) as exc:
        raise AtlasError("设置未完成，原有配置未修改。请在交互终端运行 setup。") from exc
    settings = validate(settings)
    atomic_write(home / "settings.json", json.dumps(settings, ensure_ascii=False, indent=2))
    return {
        "settings": settings,
        "saved": str(home / "settings.json"),
        "next": "配置已保存。手动执行 sync；定时运行 serve。Agent 总结需由 Agent 调度器接续 wiki/queue.json，不会由 CLI 自动调用 Agent。",
    }


def next_daily(at, timezone, timestamp):
    """Next wall-clock occurrence; skip DST gaps and never repeat the folded hour."""
    zone = ZoneInfo(timezone)
    local = datetime.fromtimestamp(timestamp, zone)
    hour, minute = map(int, at.split(":"))
    for days in range(3):
        date = local.date() + timedelta(days=days)
        candidate = datetime(date.year, date.month, date.day, hour, minute, tzinfo=zone)
        stamp = candidate.timestamp()
        roundtrip = datetime.fromtimestamp(stamp, UTC).astimezone(zone)
        if roundtrip.replace(tzinfo=None) == candidate.replace(tzinfo=None) and stamp > timestamp:
            return stamp
    raise AtlasError("无法计算下一次同步时间。")
