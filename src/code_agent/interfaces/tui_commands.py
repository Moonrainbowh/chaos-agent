from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .command_registry import REGISTRY


class TuiCommandKind(str, Enum):
    HELP = "help"; STATUS = "status"; CLEAR = "clear"; EXIT = "exit"; DIAG = "doctor"; TRACE = "trace"; NEW = "new"; SESSIONS = "sessions"; OPEN = "open"; RESTORE = "restore"; TASKS = "tasks"; PAUSE = "pause"; RESUME = "resume"; STOP = "stop"; ACCEPT = "accept"; STEER = "steer"; DIFF = "diff"; CONTEXT = "context"; TOOLS = "tools"; LANGUAGE = "language"; THEME = "theme"; COLOR = "color"; GLYPHS = "glyphs"; MODEL = "model"; SKILLS = "skills"; MCP = "mcp"; EVIDENCE = "evidence"; REWIND = "rewind"


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
    effective = {"sessions", "history", "tasks", "evidence", "profiles", "skills", "mcp"} if services is None else services
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
    kinds = {"帮助": "help", "状态": "status", "清屏": "clear", "退出": "exit", "诊断": "doctor", "追踪": "trace", "新建": "new", "会话": "sessions", "打开": "open", "恢复": "restore", "任务": "tasks", "暂停": "pause", "继续": "resume", "停止": "stop", "接受": "accept", "引导": "steer", "差异": "diff", "上下文": "context", "工具": "tools", "语言": "language", "主题": "theme", "颜色": "color", "字形": "glyphs", "模型": "model", "技能": "skills", "mcp": "mcp", "证据": "evidence", "回溯": "rewind"}
    kind = TuiCommandKind(kinds[spec.name])
    value = " ".join(arguments) or None
    if kind is TuiCommandKind.STEER and not value: return ParseOutcome(error="steering instruction is required")
    return ParseOutcome(TuiCommand(kind, value if kind in {TuiCommandKind.PAUSE, TuiCommandKind.RESUME, TuiCommandKind.STOP, TuiCommandKind.ACCEPT} else None, value if kind in {TuiCommandKind.STEER, TuiCommandKind.OPEN, TuiCommandKind.RESTORE, TuiCommandKind.LANGUAGE, TuiCommandKind.THEME, TuiCommandKind.COLOR, TuiCommandKind.GLYPHS, TuiCommandKind.MODEL, TuiCommandKind.SKILLS, TuiCommandKind.MCP, TuiCommandKind.REWIND} else None))
