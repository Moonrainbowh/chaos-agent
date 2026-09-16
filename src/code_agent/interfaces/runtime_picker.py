from __future__ import annotations

from .picker import PickerItem, PickerSource
from .command_registry import REGISTRY


def selection_blocked_reason(app: object, *, new_conversation: bool = False) -> str | None:
    runner = getattr(app, "_run_task", None)
    if runner is not None and not runner.done():
        return "Finish or pause the running task before changing its settings."
    if getattr(app, "active_task_id", None) and not new_conversation:
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
    options, current, picker_query = _options(app, runtime, kind, query)
    blocked = selection_blocked_reason(app, new_conversation=kind in {"model", "effort"})
    result = []
    for value, detail in options:
        if kind in {"model", "effort"} and value != current:
            detail += " · Opens a new conversation"
        reason = blocked
        if kind == "effort" and getattr(runtime.current, "protocol", "") == "anthropic_messages" and value != "medium":
            reason = "This provider supports only the default medium policy."
        result.append(PickerItem(
            value, _model_label(value) if kind == "model" else value, PickerSource.MODE,
            ("Current · " if value == current else "") + detail,
            enabled=reason is None, disabled_reason=reason,
            completion=prefix + value,
        ))
    result.sort(key=lambda item: item.identifier != current)
    return tuple(result), picker_query


def _options(
    app: object, runtime: object, kind: str, query: str
) -> tuple[tuple[tuple[str, str], ...], str, str]:
    if kind == "model":
        profiles = getattr(runtime, "list_profiles", None) or runtime.profiles
        values = tuple(
            (str(item[0]), "Configured · " + str(item[1])) if isinstance(item, (tuple, list))
            else (item.name, "Configured · " + item.model)
            for item in profiles()
            if not (item.name if not isinstance(item, (tuple, list)) else str(item[0])).startswith("login/")
        )
        authentication = getattr(app, "authentication", None)
        if authentication is not None:
            nested = getattr(authentication, "model_choices_for", lambda _: None)(query)
            if nested is not None:
                return tuple(nested), runtime.current.profile, _nested_query(query)
            values += tuple(getattr(authentication, "model_choices", lambda: ())())
        return values, runtime.current.profile, query
    if kind == "agent":
        return (
            (("single", "One model"), ("team", "Main agent and subagents")),
            runtime.current.topology,
            query,
        )
    labels = {
        "low": "Quick reasoning", "medium": "Balanced reasoning",
        "high": "More thorough reasoning", "xhigh": "Extra reasoning",
        "max": "Maximum reasoning",
    }
    efforts = getattr(runtime, "list_reasoning_efforts", lambda: tuple(labels))()
    return (
        tuple((value, labels.get(value, value)) for value in efforts),
        runtime.current.reasoning_effort,
        query,
    )


def _nested_query(query: str) -> str:
    parts = query.split(maxsplit=1)
    return parts[1] if len(parts) == 2 else ""


def _model_label(value: str) -> str:
    if " " not in value:
        if value.startswith("workbuddy:"):
            return "WorkBuddy · " + value.split(":", 1)[1].upper() + " · Load models"
        if value == "antigravity:oauth":
            return "Antigravity · OAuth · Browse models"
        return value
    source, model = value.split(" ", 1)
    if ":" not in source:
        return value
    provider, authentication = source.rsplit(":", 1)
    label = {"workbuddy": "WorkBuddy", "antigravity": "Antigravity"}.get(
        provider, provider
    )
    return f"{label} · {authentication.upper()} · {model}"
