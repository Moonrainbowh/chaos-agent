from __future__ import annotations

from collections.abc import Iterable, Mapping
from inspect import isawaitable

from .picker import PickerItem, PickerSource


def plugin_picker_items(
    controller: object, action: str
) -> tuple[PickerItem, ...]:
    list_plugins = getattr(controller, "list", None)
    if not callable(list_plugins):
        return ()
    try:
        values = list_plugins()
    except (KeyError, PermissionError, RuntimeError, TypeError, ValueError):
        return ()
    if isawaitable(values):
        close = getattr(values, "close", None)
        if callable(close):
            close()
        return ()
    if isinstance(values, (str, bytes, Mapping)) or not isinstance(values, Iterable):
        values = (values,)
    result = []
    for plugin in values:
        identifier = _field(plugin, "identifier", "plugin_id", "name")
        if not isinstance(identifier, str) or not identifier.strip():
            continue
        reason = _field(plugin, "disabled_reason")
        selectable = not isinstance(reason, str) or not reason
        state = _field(plugin, "status")
        if not isinstance(state, str):
            enabled = _field(plugin, "enabled")
            state = (
                "enabled"
                if enabled is True
                else "disabled"
                if enabled is False
                else ""
            )
        result.append(
            PickerItem(
                identifier,
                identifier,
                PickerSource.PLUGIN,
                state,
                enabled=selectable,
                disabled_reason=reason if not selectable else None,
                completion=f"/插件 {action} {identifier}",
            )
        )
    return tuple(result)


def _field(value: object, *names: str) -> object:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return None
