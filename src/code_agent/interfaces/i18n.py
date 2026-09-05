from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import locale
import os
from collections.abc import Mapping


class Language(str, Enum):
    ZH_CN = "zh-CN"
    EN_US = "en"


@dataclass(frozen=True)
class UiCatalog:
    language: Language
    task: str
    paused: str
    resume: str
    stop: str


ZH_CN = UiCatalog(Language.ZH_CN, "任务", "暂停", "继续", "停止")
EN_US = UiCatalog(Language.EN_US, "Task", "Pause", "Resume", "Stop")


def select_language(system_locale: str | None, explicit: str | None) -> Language:
    choice = explicit if explicit and explicit != "auto" else system_locale
    if isinstance(choice, str):
        lowered = choice.lower().replace("_", "-")
        if lowered.startswith("zh") or "chinese" in lowered:
            return Language.ZH_CN
    return Language.EN_US


def select_runtime_language(environ: Mapping[str, str] | None = None) -> Language:
    """Use English UI by default; retain an explicit language override."""
    source = os.environ if environ is None else environ
    system_locale = locale.getlocale()[0]
    explicit = source.get("CHAOS_LANGUAGE") or source.get("CODE_AGENT_LANGUAGE") or "en"
    return select_language(system_locale, explicit)


def catalog_for(language: Language) -> UiCatalog:
    return ZH_CN if language is Language.ZH_CN else EN_US


def localize_task_status(status: str, catalog: UiCatalog) -> str:
    if catalog.language is Language.EN_US:
        return status.replace("_", " ")
    return {
        "created": "已创建",
        "running": "执行中",
        "verifying": "验证中",
        "waiting_decision": "等待决定",
        "paused": "已暂停",
        "completed": "已完成",
        "accepted_partial": "已接受部分交付",
        "failed": "失败",
        "interrupted": "已中断",
    }.get(status, status)
