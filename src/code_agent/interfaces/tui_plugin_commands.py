from __future__ import annotations

from collections.abc import Iterable, Mapping
from inspect import isawaitable
from typing import Any

from .terminal_display import DisplayKind, safe_text


async def handle_plugin_command(app: Any, instruction: str | None) -> bool:
    controller = getattr(app, "plugins", None)
    if controller is None:
        app._append(DisplayKind.ERROR, "Plugins are unavailable")
        return False
    action, _, identifier = (instruction or "list").partition(" ")
    try:
        if action in {"list", "列表"} and not identifier:
            values = await _invoke(controller, "list")
            _append_values(app, values, "No Plugins")
        elif action in {"status", "状态"}:
            arguments = (identifier,) if identifier else ()
            values = await _invoke(controller, "status", *arguments)
            _append_values(app, values, "No Plugins")
        elif action in {"enable", "启用"} and identifier:
            await _invoke(controller, "enable", identifier)
            app._append(DisplayKind.METADATA, f"Plugin enabled: {safe_text(identifier)}")
        elif action in {"disable", "禁用"} and identifier:
            await _invoke(controller, "disable", identifier)
            app._append(DisplayKind.METADATA, f"Plugin disabled: {safe_text(identifier)}")
        elif action in {"reload", "重载"} and not identifier:
            values = await _invoke(controller, "reload")
            _append_values(app, values, "Plugins reloaded")
        else:
            app._append(DisplayKind.ERROR, "Plugin command arguments are invalid")
            return False
    except (AttributeError, KeyError, PermissionError, RuntimeError, TypeError, ValueError) as error:
        app._append(DisplayKind.ERROR, type(error).__name__)
        return False
    return True


async def _invoke(controller: object, method: str, *arguments: str) -> object:
    target = getattr(controller, method)
    value = target(*arguments)
    return await value if isawaitable(value) else value


def _append_values(app: Any, values: object, empty: str) -> None:
    if values is None:
        app._append(DisplayKind.METADATA, empty)
        return
    if isinstance(values, (str, bytes, Mapping)) or not isinstance(values, Iterable):
        values = (values,)
    rendered = " | ".join(_summary(value) for value in values)
    app._append(DisplayKind.METADATA, rendered or empty)


def _summary(value: object) -> str:
    identifier = _field(value, "identifier", "plugin_id", "name")
    if not isinstance(identifier, str):
        return safe_text(str(value))[:256]
    state = _field(value, "status")
    if not isinstance(state, str):
        enabled = _field(value, "enabled")
        state = "enabled" if enabled is True else "disabled" if enabled is False else ""
    return safe_text(identifier + (f":{state}" if state else ""))[:256]


def _field(value: object, *names: str) -> object:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return None
