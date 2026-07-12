from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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
    if isinstance(choice, str) and choice.lower().replace("_", "-").startswith("zh"):
        return Language.ZH_CN
    return Language.EN_US
