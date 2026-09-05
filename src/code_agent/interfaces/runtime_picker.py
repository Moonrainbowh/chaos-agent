from __future__ import annotations

from .picker import PickerItem, PickerSource
from .command_registry import REGISTRY


def selection_blocked_reason(app: object) -> str | None:
    runner = getattr(app, "_run_task", None)
    if runner is not None and not runner.done():
        return "Finish or pause the running task before changing its settings."
    if getattr(app, "active_task_id", None):
        return "This task keeps its saved settings. Use /new to select settings for a new task."
    return None


def runtime_picker_items(app: object) -> tuple[tuple[PickerItem, ...], str] | None:
    text = app.input.text
    if not text.startswith(("/", ":")) or " " not in text:
        return None
    name, query = text[1:].split(" ", 1)
    registry = getattr(app, "command_registry", REGISTRY)
    spec = registry.resolve(name)
    if spec is None:
        return None
    kind = spec.name
    prefix = text[0] + kind + " "
    if kind == "mode" and " " in query:
        action, query = query.split(" ", 1)
        resolved = registry.resolve_action(spec, action)
        kind = resolved.name if resolved else ""
        prefix += kind + " "
    if kind not in {"model", "effort", "agent"}:
        return None
    runtime = getattr(app, "runtime_selection", None)
    if runtime is None:
        return (), query
    options, current = _options(runtime, kind)
    blocked = selection_blocked_reason(app)
    result = []
    for value, detail in options:
        reason = blocked
        if kind == "effort" and getattr(runtime.current, "protocol", "") == "anthropic_messages" and value != "medium":
            reason = "This provider supports only the default medium policy."
        result.append(PickerItem(
            value, value, PickerSource.MODE,
            ("Current · " if value == current else "") + detail,
            enabled=reason is None, disabled_reason=reason,
            completion=prefix + value,
        ))
    result.sort(key=lambda item: item.identifier != current)
    return tuple(result), query


def _options(runtime: object, kind: str) -> tuple[tuple[tuple[str, str], ...], str]:
    if kind == "model":
        profiles = getattr(runtime, "list_profiles", None) or runtime.profiles
        values = tuple(
            (str(item[0]), str(item[1])) if isinstance(item, (tuple, list))
            else (item.name, item.model)
            for item in profiles()
        )
        return values, runtime.current.profile
    if kind == "agent":
        return (("single", "One model"), ("team", "Main agent and subagents")), runtime.current.topology
    labels = {
        "low": "Quick reasoning", "medium": "Balanced reasoning",
        "high": "More thorough reasoning", "xhigh": "Extra reasoning",
        "max": "Maximum reasoning",
    }
    efforts = getattr(runtime, "list_reasoning_efforts", lambda: tuple(labels))()
    return tuple((value, labels.get(value, value)) for value in efforts), runtime.current.reasoning_effort
