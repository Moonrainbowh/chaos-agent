from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .command_registry import CommandRegistry, REGISTRY

_DEFAULT_SERVICES = {
    "authentication",
    "sessions",
    "history",
    "tasks",
    "evidence",
    "checkpoints",
    "modes",
    "permissions",
    "workflows",
    "skills",
    "mcp",
    "plugins",
    "attachments",
    "runtime_selection",
    "task_modes",
    "semantic_graph",
    "peers",
}


class TuiCommandKind(str, Enum):
    LOGIN = "login"
    LOGSWITCH = "logswitch"
    THEME = "theme"
    HELP = "help"; STATUS = "status"; CLEAR = "clear"; COMPACT = "compact"; EXIT = "exit"; NEW = "new"; SESSIONS = "sessions"; RESTORE = "restore"; TASKS = "tasks"; ACCEPT = "accept"; DIFF = "diff"; MAP = "map"; ATTACHMENT = "attachment"; EVIDENCE = "evidence"; CHECKPOINT = "checkpoint"; REWIND = "rewind"; MODEL = "model"; MODE = "mode"; EFFORT = "effort"; PERMISSION = "permission"; WORKFLOW = "workflow"; SKILL = "skill"; MCP = "mcp"; PLUGIN_CONTROL = "plugin_control"; PLUGIN = "plugin"; COST = "cost"; DOCTOR = "doctor"; REVIEW = "review"; TEST = "test"


@dataclass(frozen=True)
class TuiCommand:
    kind: TuiCommandKind
    task_id: str | None = None
    instruction: str | None = None
    command_name: str | None = None
    action: str | None = None

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
    if not text.startswith(("/", ":")): return ParseOutcome()
    text = "/" + text[1:]
    text = _compatibility_alias(text)
    effective = _DEFAULT_SERVICES if services is None else services
    raw_body = text[1:].lstrip()
    head = raw_body.split(maxsplit=1)[0] if raw_body else ""
    raw_spec = registry.resolve(head)
    if raw_spec is not None and raw_spec.name in {"rewind", "回退", "attach", "附件"}:
        if not set(raw_spec.requires).issubset(effective):
            return ParseOutcome(error="unknown or unavailable slash command")
        instruction = raw_body[len(head):].lstrip() or None
        kind = (
            TuiCommandKind.REWIND
            if raw_spec.name in {"rewind", "回退"}
            else TuiCommandKind.ATTACHMENT
        )
        return ParseOutcome(TuiCommand(kind, instruction=instruction))
    spec, arguments, error = registry.parse(text, effective)
    if error: return ParseOutcome(error=error)
    assert spec is not None
    if arguments and not spec.usage:
        return ParseOutcome(error="command does not accept arguments")
    normalized_action = None
    if spec.actions and arguments:
        action = registry.resolve_action(spec, arguments[0])
        if action is None:
            return ParseOutcome(error="unknown slash command action")
        if not set(action.requires).issubset(effective):
            return ParseOutcome(error="unknown or unavailable slash command action")
        if len(arguments) > 1 and not action.usage:
            return ParseOutcome(error="command action does not accept arguments")
        if len(arguments) == 1 and action.usage.startswith("<"):
            return ParseOutcome(error="command action argument is required")
        normalized_action = action.name
    return _parsed_command(spec, arguments, normalized_action)


def _parsed_command(spec: object, arguments: tuple[str, ...], normalized_action: str | None) -> ParseOutcome:
    kinds = {
        "login": "login", "logswitch": "logswitch",
        "theme": "theme", "help": "help", "status": "status", "clear": "clear", "compact": "compact",
        "exit": "exit", "new": "new", "sessions": "sessions", "restore": "restore",
        "tasks": "tasks", "accept": "accept", "diff": "diff", "map": "map", "attach": "attachment",
        "evidence": "evidence", "checkpoint": "checkpoint", "rewind": "rewind",
        "model": "model", "mode": "mode", "effort": "effort",
        "permission": "permission", "workflow": "workflow", "flow": "workflow",
        "skill": "skill", "mcp": "mcp", "plugin": "plugin_control",
        "cost": "cost", "doctor": "doctor", "review": "review", "test": "test",
        # Legacy/Chinese aliases
        "帮助": "help", "状态": "status", "清屏": "clear", "退出": "exit",
        "新建": "new", "会话": "sessions", "恢复": "restore", "任务": "tasks",
        "接受": "accept", "差异": "diff", "附件": "attachment", "证据": "evidence",
        "检查点": "checkpoint", "回退": "rewind", "图谱": "map", "模式": "mode", "权限": "permission",
        "流程": "workflow", "技能": "skill", "插件": "plugin_control",
    }
    kind = TuiCommandKind.PLUGIN if spec.source == "plugin" else TuiCommandKind(kinds[spec.name])
    value = " ".join(arguments) or None
    instructions = {
        TuiCommandKind.LOGIN, TuiCommandKind.LOGSWITCH,
        TuiCommandKind.HELP, TuiCommandKind.SESSIONS, TuiCommandKind.RESTORE,
        TuiCommandKind.ATTACHMENT, TuiCommandKind.EVIDENCE,
        TuiCommandKind.CHECKPOINT, TuiCommandKind.REWIND,
        TuiCommandKind.MODEL, TuiCommandKind.MODE, TuiCommandKind.EFFORT,
        TuiCommandKind.PERMISSION, TuiCommandKind.WORKFLOW,
        TuiCommandKind.SKILL, TuiCommandKind.MCP,
        TuiCommandKind.PLUGIN_CONTROL, TuiCommandKind.PLUGIN,
        TuiCommandKind.COMPACT, TuiCommandKind.COST,
        TuiCommandKind.DOCTOR, TuiCommandKind.REVIEW, TuiCommandKind.TEST,
        TuiCommandKind.MAP, TuiCommandKind.THEME,
    }
    return ParseOutcome(
        TuiCommand(
            kind,
            value if kind is TuiCommandKind.ACCEPT else None,
            value if kind in instructions else None,
            spec.name if kind is TuiCommandKind.PLUGIN else None,
            normalized_action,
        )
    )


def _compatibility_alias(text: str) -> str:
    body = text[1:].lstrip()
    if not body:
        return text
    parts = body.split(maxsplit=1)
    head = parts[0].casefold()
    tail = " " + parts[1] if len(parts) == 2 else ""
    if head in {"list-agents", "peers"}:
        return "/sessions online" + tail
    if head == "rename":
        return "/sessions rename" + tail
    return text
