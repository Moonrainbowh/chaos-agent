from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .command_registry import REGISTRY


class TuiCommandKind(str, Enum):
    HELP = "help"; STATUS = "status"; CLEAR = "clear"; EXIT = "exit"; NEW = "new"; SESSIONS = "sessions"; RESTORE = "restore"; TASKS = "tasks"; ACCEPT = "accept"; DIFF = "diff"; EVIDENCE = "evidence"; MODE = "mode"; REWIND = "rewind"


@dataclass(frozen=True)
class TuiCommand:
    kind: TuiCommandKind
    task_id: str | None = None
    instruction: str | None = None

@dataclass(frozen=True)
class ParseOutcome:
    command: TuiCommand | None = None
    error: str | None = None
    @property
    def is_command(self) -> bool: return self.command is not None


def parse_tui_command(text: str, services: set[str] | None = None) -> ParseOutcome:
    if not isinstance(text, str): raise TypeError("text must be a string")
    if not text.startswith("/"): return ParseOutcome()
    effective = {"sessions", "history", "tasks", "evidence", "modes"} if services is None else services
    raw_body = text[1:].lstrip()
    head = raw_body.split(maxsplit=1)[0] if raw_body else ""
    rewind_spec = REGISTRY.resolve(head)
    if rewind_spec is not None and rewind_spec.name == "回溯":
        if not set(rewind_spec.requires).issubset(effective):
            return ParseOutcome(error="unknown or unavailable slash command")
        instruction = raw_body[len(head):].lstrip() or None
        return ParseOutcome(TuiCommand(TuiCommandKind.REWIND, instruction=instruction))
    spec, arguments, error = REGISTRY.parse(text, effective)
    if error: return ParseOutcome(error=error)
    assert spec is not None
    if arguments and not spec.usage:
        return ParseOutcome(error="command does not accept arguments")
    if spec.actions and arguments:
        action = REGISTRY.resolve_action(spec, arguments[0])
        if action is None:
            return ParseOutcome(error="unknown slash command action")
        if len(arguments) > 1 and not action.usage:
            return ParseOutcome(error="command action does not accept arguments")
        if len(arguments) == 1 and action.usage.startswith("<"):
            return ParseOutcome(error="command action argument is required")
    kinds = {"帮助": "help", "状态": "status", "清屏": "clear", "退出": "exit", "新建": "new", "会话": "sessions", "恢复": "restore", "任务": "tasks", "接受": "accept", "差异": "diff", "证据": "evidence", "模式": "mode", "回溯": "rewind"}
    kind = TuiCommandKind(kinds[spec.name])
    value = " ".join(arguments) or None
    return ParseOutcome(TuiCommand(
        kind,
        value if kind is TuiCommandKind.ACCEPT else None,
        value if kind in {
            TuiCommandKind.HELP,
            TuiCommandKind.RESTORE,
            TuiCommandKind.EVIDENCE,
            TuiCommandKind.MODE,
            TuiCommandKind.REWIND,
        } else None,
    ))
