from __future__ import annotations

from typing import Any

from .terminal_display import DisplayKind
from .runtime_picker import selection_blocked_reason
from .tui_new_conversation import reset_conversation


async def set_model(app: Any, instruction: str | None) -> bool:
    runtime = getattr(app, "runtime_selection", None)
    if runtime is None:
        app._append(DisplayKind.ERROR, "model selection is unavailable")
        return False
    if instruction is None:
        app.input.replace("/model ")
        return True
    try:
        profile = _resolve_profile(instruction, runtime.profiles())
        selected = await _select_for_new_conversation(app, profile=profile)
    except (RuntimeError, TypeError, ValueError) as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
    app._append(DisplayKind.METADATA, "model selected: " + _runtime_summary(selected) + _host_runtime_suffix(app))
    return True


async def set_effort(app: Any, instruction: str | None) -> bool:
    runtime = getattr(app, "runtime_selection", None)
    if runtime is None:
        app._append(DisplayKind.ERROR, "reasoning effort selection is unavailable")
        return False
    choices = tuple(
        getattr(runtime, "list_reasoning_efforts", lambda: ("low", "medium", "high", "xhigh", "max"))()
    )
    if instruction is None:
        app.input.replace("/effort ")
        return True
    if instruction not in choices:
        app._append(DisplayKind.ERROR, "reasoning effort must be " + ", ".join(choices))
        return False
    try:
        selected = await _select_for_new_conversation(app, reasoning_effort=instruction)
    except (RuntimeError, TypeError, ValueError) as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
    app._append(DisplayKind.METADATA, "reasoning effort selected: " + _runtime_summary(selected) + _host_runtime_suffix(app))
    return True


async def set_task_mode(
    app: Any, instruction: str | None, action: str | None
) -> bool | None:
    if action not in {"ask", "code", "plan"}:
        return None
    control = getattr(app, "task_modes", None)
    if control is None:
        app._append(DisplayKind.ERROR, "task mode selection is unavailable")
        return False
    try:
        selected = await control.use(action, idle=_idle(app))
    except (RuntimeError, ValueError) as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
    app._append(
        DisplayKind.METADATA,
        f"task mode selected: {selected.name} · {selected.description}",
    )
    return True


def show_task_modes(app: Any) -> bool:
    control = getattr(app, "task_modes", None)
    if control is None:
        app._append(DisplayKind.ERROR, "task mode selection is unavailable")
        return False
    choices = " | ".join(item.name for item in control.list())
    app._append(DisplayKind.METADATA, f"current {control.current.name} | {choices}")
    return True


async def _select_for_new_conversation(app: Any, **values: str) -> object:
    """Apply validated settings first; detach saved context only on success."""
    idle = _idle(app, new_conversation=True)
    runtime = app.runtime_selection
    current = runtime.current
    if all(getattr(current, key) == value for key, value in values.items()):
        return current
    selected = await runtime.use(**values, idle=idle)
    reset_conversation(app)
    app._append(DisplayKind.METADATA, "New conversation opened; previous messages and context are not carried over.")
    return selected


def _idle(app: Any, *, new_conversation: bool = False) -> bool:
    reason = selection_blocked_reason(app, new_conversation=new_conversation)
    if reason:
        raise RuntimeError(reason)
    return app._run_task is None or app._run_task.done()


def _resolve_profile(query: str, profiles: object) -> str:
    names = tuple(_profile_name(item) for item in profiles)
    folded = query.casefold()
    exact = tuple(name for name in names if name.casefold() == folded)
    suffix = tuple(
        name for name in names
        if name.casefold().endswith(("_" + folded, "-" + folded, "." + folded))
    )
    matches = exact or suffix
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"unknown model profile: {query}")
    raise ValueError(f"ambiguous model profile: {query}")


def _profile_name(profile: object) -> str:
    return str(profile[0] if isinstance(profile, (tuple, list)) else profile.name)


def _profile_model(profile: object) -> str:
    return str(profile[1] if isinstance(profile, (tuple, list)) else profile.model)


def _runtime_summary(selection: object) -> str:
    return " · ".join(
        str(getattr(selection, name))
        for name in ("topology", "profile", "model", "reasoning_effort")
    )


def _host_runtime_suffix(app: object) -> str:
    summary = getattr(app, "host_runtime_summary", None)
    if not isinstance(summary, str) or not summary.strip():
        return ""
    return " · host: " + summary
