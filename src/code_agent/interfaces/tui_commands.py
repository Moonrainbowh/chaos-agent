from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TuiCommandKind(str, Enum):
    TASKS = "tasks"; PAUSE = "pause"; RESUME = "resume"; STOP = "stop"; ACCEPT = "accept"; STEER = "steer"; DIFF = "diff"; LANGUAGE = "language"; STATUS = "status"; HELP = "help"; THEME = "theme"; COLOR = "color"; GLYPHS = "glyphs"; MODEL = "model"; SKILLS = "skills"; MCP = "mcp"; EVIDENCE = "evidence"


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


def parse_tui_command(text: str) -> ParseOutcome:
    if not isinstance(text, str): raise TypeError("text must be a string")
    if not text.startswith("/"): return ParseOutcome()
    parts = text[1:].strip().split(maxsplit=1)
    if not parts: return ParseOutcome(error="slash command is required")
    aliases = {"任务": TuiCommandKind.TASKS, "tasks": TuiCommandKind.TASKS, "暂停": TuiCommandKind.PAUSE, "pause": TuiCommandKind.PAUSE, "继续": TuiCommandKind.RESUME, "resume": TuiCommandKind.RESUME, "停止": TuiCommandKind.STOP, "stop": TuiCommandKind.STOP, "接受": TuiCommandKind.ACCEPT, "accept": TuiCommandKind.ACCEPT, "引导": TuiCommandKind.STEER, "steer": TuiCommandKind.STEER, "差异": TuiCommandKind.DIFF, "diff": TuiCommandKind.DIFF, "语言": TuiCommandKind.LANGUAGE, "language": TuiCommandKind.LANGUAGE, "状态": TuiCommandKind.STATUS, "status": TuiCommandKind.STATUS, "帮助": TuiCommandKind.HELP, "help": TuiCommandKind.HELP, "主题": TuiCommandKind.THEME, "theme": TuiCommandKind.THEME, "颜色": TuiCommandKind.COLOR, "color": TuiCommandKind.COLOR, "字形": TuiCommandKind.GLYPHS, "glyphs": TuiCommandKind.GLYPHS, "模型": TuiCommandKind.MODEL, "model": TuiCommandKind.MODEL, "技能": TuiCommandKind.SKILLS, "skills": TuiCommandKind.SKILLS, "mcp": TuiCommandKind.MCP, "证据": TuiCommandKind.EVIDENCE, "evidence": TuiCommandKind.EVIDENCE}
    kind = aliases.get(parts[0].lower())
    if kind is None: return ParseOutcome(error="unknown slash command")
    value = parts[1] if len(parts) == 2 else None
    if kind is TuiCommandKind.STEER and not value: return ParseOutcome(error="steering instruction is required")
    return ParseOutcome(TuiCommand(kind, value if kind in {TuiCommandKind.PAUSE, TuiCommandKind.RESUME, TuiCommandKind.STOP, TuiCommandKind.ACCEPT} else None, value if kind in {TuiCommandKind.STEER, TuiCommandKind.LANGUAGE, TuiCommandKind.THEME, TuiCommandKind.COLOR, TuiCommandKind.GLYPHS, TuiCommandKind.MODEL, TuiCommandKind.SKILLS, TuiCommandKind.MCP} else None))
