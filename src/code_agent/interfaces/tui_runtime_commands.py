from __future__ import annotations

import asyncio
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
        authentication = getattr(app, "authentication", None)
        if authentication is not None and getattr(
            authentication, "is_refresh_choice", lambda _: False
        )(instruction):
            return await _start_workbuddy_refresh(app, authentication, instruction)
        if authentication is not None and getattr(
            authentication, "is_antigravity_catalog_choice", lambda _: False
        )(instruction):
            _idle(app, new_conversation=True)
            app.input.replace("/model antigravity:oauth ")
            return True
        if authentication is not None and getattr(
            authentication, "is_saved_model_choice", lambda _: False
        )(instruction):
            profile = await authentication.select_model(instruction)
        else:
            profile = _resolve_profile(instruction, runtime.profiles())
        selected = await _select_for_new_conversation(app, profile=profile)
        _remember_model_selection(app, profile)
    except (RuntimeError, TypeError, ValueError) as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
    app._append(DisplayKind.METADATA, "model selected: " + _runtime_summary(selected) + _host_runtime_suffix(app))
    return True


async def refresh_workbuddy_models(
    app: Any, authentication: Any, instruction: str
) -> bool:
    app._append(DisplayKind.METADATA, "Loading WorkBuddy account models · Esc cancels")
    try:
        count = await authentication.refresh_models(instruction)
    except asyncio.CancelledError:
        app._append(DisplayKind.METADATA, "Model loading cancelled; login is retained.")
        return False
    except Exception:
        app._append(
            DisplayKind.ERROR,
            "WorkBuddy model discovery failed or returned no runnable models. "
            "Login is retained; select its load item in /model to retry.",
        )
        app.input.replace("/model " + instruction.strip())
        return False
    app._append(DisplayKind.METADATA, f"Loaded {count} WorkBuddy models; choose one below.")
    app.input.replace("/model " + instruction.strip() + " ")
    return True


async def _start_workbuddy_refresh(
    app: Any, authentication: Any, instruction: str
) -> bool:
    _idle(app, new_conversation=True)
    pending = getattr(app, "_auth_task", None)
    if pending is not None and not pending.done():
        raise RuntimeError("Login is in progress; Esc cancels it.")

    async def refresh() -> None:
        try:
            await refresh_workbuddy_models(app, authentication, instruction)
        finally:
            if getattr(app, "_auth_task", None) is asyncio.current_task():
                app._auth_task = None
            app.redraw()

    app._auth_task = asyncio.create_task(refresh())
    await asyncio.sleep(0)
    return True


def _remember_model_selection(app: Any, profile: str) -> None:
    store = getattr(app, "model_preferences", None)
    if store is None:
        return
    authentication = getattr(app, "authentication", None)
    try:
        preference_for_profile = getattr(authentication, "preference_for_profile", None)
        preference = preference_for_profile(profile) if callable(preference_for_profile) else None
        if preference is not None:
            store.save(preference)
    except (OSError, ValueError):
        app._append(
            DisplayKind.WARNING,
            "Model selected, but it could not be remembered for the next startup.",
        )


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
    if action not in {"auto", "ask", "code", "plan"}:
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
