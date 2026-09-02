from __future__ import annotations

from dataclasses import replace

from .command_registry import REGISTRY
from .picker import mcp_picker_items, skill_picker_items
from .plugin_picker import plugin_picker_items

_SKILL_COMMANDS = {"/技能", "/skill", "/skills"}
_SKILL_ACTIONS = {"信息", "info", "启用", "enable", "禁用", "disable", "来源", "source"}
_MCP_ACTIONS = {
    "status", "状态", "tools", "工具", "enable", "启用", "disable", "禁用",
    "restart", "重启", "diagnose", "诊断",
}
_PLUGIN_COMMANDS = {"/插件", "/plugin", "/plugins"}
_PLUGIN_ACTIONS = {"status", "状态", "enable", "启用", "disable", "禁用"}


def picker_context(
    text: str, registry: object = REGISTRY
) -> tuple[object | None, str]:
    if not text.startswith(("/", ":")):
        return None, ""
    body = text[1:]
    head, remainder = _split_token(body)
    if remainder is None:
        return None, body
    spec = registry.resolve(head)
    if spec is None or not spec.actions:
        return None, body
    return spec, remainder


def dynamic_picker_items(
    app: object,
) -> tuple[tuple[object, ...], str] | None:
    parsed = _dynamic_request(app.input.text)
    if parsed is None:
        return None
    command, action, query = parsed
    items = _resource_picker_items(app, command, action)
    if items is not None and app.input.text.startswith(":"):
        items = tuple(
            replace(
                item,
                label=":" + item.label[1:] if item.label.startswith("/") else item.label,
                completion=(
                    ":" + item.completion[1:]
                    if item.completion and item.completion.startswith("/")
                    else item.completion
                ),
            )
            for item in items
        )
    return None if items is None else (items, query)


def _resource_picker_items(
    app: object, command: str, action: str
) -> tuple[object, ...] | None:
    if command in _SKILL_COMMANDS and action in _SKILL_ACTIONS:
        controller = getattr(app, "skills", None)
        return skill_picker_items(controller, action) if controller is not None else ()
    if command == "/mcp" and action in _MCP_ACTIONS:
        controller = getattr(app, "mcp", None)
        return mcp_picker_items(controller, action) if controller is not None else ()
    if command in _PLUGIN_COMMANDS and action in _PLUGIN_ACTIONS:
        controller = getattr(app, "plugins", None)
        return plugin_picker_items(controller, action) if controller is not None else ()
    return None


def _dynamic_request(text: str) -> tuple[str, str, str] | None:
    if not text.startswith(("/", ":")):
        return None
    command, remainder = _split_token(text[1:])
    if remainder is None:
        return None
    action, query = _split_token(remainder)
    if query is None:
        return None
    return "/" + command.casefold(), action.casefold(), query


def _split_token(value: str) -> tuple[str, str | None]:
    for index, character in enumerate(value):
        if character.isspace():
            return value[:index], value[index + 1 :].lstrip()
    return value, None
