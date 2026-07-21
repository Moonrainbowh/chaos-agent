from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .command_registry import CommandRegistry, REGISTRY


class TuiCommandKind(str, Enum):
    HELP = "help"; STATUS = "status"; CLEAR = "clear"; EXIT = "exit"; NEW = "new"; SESSIONS = "sessions"; RESTORE = "restore"; TASKS = "tasks"; ACCEPT = "accept"; DIFF = "diff"; EVIDENCE = "evidence"; MODE = "mode"; PERMISSION = "permission"; WORKFLOW = "workflow"; SKILL = "skill"; MCP = "mcp"; PLUGIN = "plugin"


@dataclass(frozen=True)
class TuiCommand:
    kind: TuiCommandKind
    task_id: str | None = None
    instruction: str | None = None
    command_name: str | None = None

@dataclass(frozen=True)
class ParseOutcome:
    command: TuiCommand | None = None
    error: str | None = None
    @property
    def is_command(self) -> bool: return self.command is not None


def parse_tui_command(
    text: str,
    services: set[str] | None = None,
    registry: CommandRegistry = REGISTRY,
) -> ParseOutcome:
    if not isinstance(text, str): raise TypeError("text must be a string")
    spec, arguments, error = registry.parse(text, services or {"sessions", "history", "tasks", "evidence", "modes", "permissions", "workflows", "skills", "mcp"})
    if not text.startswith("/"): return ParseOutcome()
    if error: return ParseOutcome(error=error)
    assert spec is not None
    if arguments and not spec.usage:
        return ParseOutcome(error="command does not accept arguments")
    if spec.actions and arguments:
        action = registry.resolve_action(spec, arguments[0])
        if action is None:
            return ParseOutcome(error="unknown slash command action")
        if len(arguments) > 1 and not action.usage:
            return ParseOutcome(error="command action does not accept arguments")
        if len(arguments) == 1 and action.usage.startswith("<"):
            return ParseOutcome(error="command action argument is required")
    kinds = {"帮助": "help", "状态": "status", "清屏": "clear", "退出": "exit", "新建": "new", "会话": "sessions", "恢复": "restore", "任务": "tasks", "接受": "accept", "差异": "diff", "证据": "evidence", "模式": "mode", "权限": "permission", "流程": "workflow", "技能": "skill", "mcp": "mcp"}
    kind = TuiCommandKind.PLUGIN if spec.source == "plugin" else TuiCommandKind(kinds[spec.name])
    value = " ".join(arguments) or None
    return ParseOutcome(TuiCommand(kind, value if kind is TuiCommandKind.ACCEPT else None, value if kind in {TuiCommandKind.HELP, TuiCommandKind.RESTORE, TuiCommandKind.EVIDENCE, TuiCommandKind.MODE, TuiCommandKind.PERMISSION, TuiCommandKind.WORKFLOW, TuiCommandKind.SKILL, TuiCommandKind.MCP, TuiCommandKind.PLUGIN} else None, spec.name if kind is TuiCommandKind.PLUGIN else None))
