from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TuiCommandKind(str, Enum):
    TASKS = "tasks"; PAUSE = "pause"; RESUME = "resume"; STOP = "stop"; STEER = "steer"; DIFF = "diff"; LANGUAGE = "language"


@dataclass(frozen=True)
class TuiCommand:
    kind: TuiCommandKind
    task_id: str | None = None
    instruction: str | None = None


def parse_tui_command(text: str) -> TuiCommand | None:
    if not isinstance(text, str): raise TypeError("text must be a string")
    if not text.startswith("/"): return None
    parts = text[1:].strip().split(maxsplit=1)
    if not parts: raise ValueError("slash command is required")
    aliases = {"任务": TuiCommandKind.TASKS, "tasks": TuiCommandKind.TASKS, "暂停": TuiCommandKind.PAUSE, "pause": TuiCommandKind.PAUSE, "继续": TuiCommandKind.RESUME, "resume": TuiCommandKind.RESUME, "停止": TuiCommandKind.STOP, "stop": TuiCommandKind.STOP, "引导": TuiCommandKind.STEER, "steer": TuiCommandKind.STEER, "差异": TuiCommandKind.DIFF, "diff": TuiCommandKind.DIFF, "语言": TuiCommandKind.LANGUAGE, "language": TuiCommandKind.LANGUAGE}
    try: kind = aliases[parts[0].lower()]
    except KeyError: raise ValueError("unknown slash command") from None
    value = parts[1] if len(parts) == 2 else None
    if kind in {TuiCommandKind.PAUSE, TuiCommandKind.RESUME, TuiCommandKind.STOP} and not value: raise ValueError("task ID is required")
    if kind is TuiCommandKind.STEER and not value: raise ValueError("steering instruction is required")
    return TuiCommand(kind, value if kind in {TuiCommandKind.PAUSE, TuiCommandKind.RESUME, TuiCommandKind.STOP} else None, value if kind in {TuiCommandKind.STEER, TuiCommandKind.LANGUAGE} else None)
