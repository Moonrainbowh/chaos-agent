"""Login and temporary credential selection through an injected controller."""
from __future__ import annotations

import asyncio

from .picker import PickerItem, PickerSource
from .runtime_picker import selection_blocked_reason
from .terminal_display import DisplayKind
from .tui_runtime_commands import set_model
from .tui_new_conversation import reset_conversation


def auth_picker_items(app: object) -> tuple[tuple[PickerItem, ...], str] | None:
    text = app.input.text
    if not text.startswith(("/", ":")) or " " not in text:
        return None
    name, query = text[1:].split(" ", 1)
    if name not in {"login", "logswitch"}:
        return None
    controller = getattr(app, "authentication", None)
    if controller is None:
        return (), query
    try:
        choices = controller.login_choices() if name == "login" else controller.switch_choices()
    except Exception:
        return (PickerItem("unavailable", "Login choices unavailable", PickerSource.MODE,
                           "Check the saved credentials/model catalog and retry.", enabled=False,
                           disabled_reason="Could not read login choices."),), query
    reason = selection_blocked_reason(app, new_conversation=True)
    current = getattr(getattr(app.runtime_selection, "current", None), "profile", None)
    items = tuple(PickerItem(
        value, value, PickerSource.MODE,
        ("Current · " if value == current else "") + detail,
        completion=text[0] + name + " " + value,
        enabled=reason is None, disabled_reason=reason,
    ) for value, detail in choices)
    return items, query


async def handle_auth_command(app: object, name: str, instruction: str | None) -> bool:
    controller = getattr(app, "authentication", None)
    if controller is None:
        app._append(DisplayKind.ERROR, "Authentication is unavailable.")
        return False
    reason = selection_blocked_reason(app, new_conversation=True)
    pending = getattr(app, "_auth_task", None)
    if reason or (pending is not None and not pending.done()):
        app._append(DisplayKind.ERROR, reason or "Login is in progress; Esc cancels it.")
        return False
    if not instruction:
        app.input.replace("/" + name + " ")
        return True
    if name == "login":
        app._auth_task = asyncio.create_task(_login(app, controller, instruction))
        await asyncio.sleep(0)
        return True
    if getattr(controller, "is_refresh_choice", lambda _: False)(instruction):
        app._auth_task = asyncio.create_task(_refresh_models(app, controller, instruction))
        await asyncio.sleep(0)
        return True
    try:
        profile = await controller.switch(instruction)
    except Exception:
        app._append(DisplayKind.ERROR, "Login/model selection failed; check saved credentials and model choice.")
        return False
    return await set_model(app, profile)


async def _refresh_models(app, controller, instruction):
    app._append(DisplayKind.METADATA, "Loading WorkBuddy account models · Esc cancels")
    try:
        count = await controller.refresh_models(instruction)
        app._append(DisplayKind.METADATA, f"Loaded {count} WorkBuddy models; choose one below.")
        app.input.replace("/logswitch " + instruction.strip() + " ")
    except asyncio.CancelledError:
        app._append(DisplayKind.METADATA, "Model loading cancelled; login is retained.")
    except Exception:
        app._append(DisplayKind.ERROR, "WorkBuddy model discovery failed or returned no runnable models. Login is retained; select its load/refresh entry to retry.")
        app.input.replace("/logswitch " + instruction.strip())
    finally:
        if getattr(app, "_auth_task", None) is asyncio.current_task():
            app._auth_task = None
        app.redraw()


async def _login(app: object, controller: object, instruction: str) -> None:
    from .tui_auth_prompt import read_auth_input

    def display(message: str) -> None:
        app._append(DisplayKind.METADATA, message)
        app.redraw()

    try:
        current = app.runtime_selection.current.profile
        affects_current = getattr(controller, "affects_profile", lambda *_: False)(instruction, current)
        result = await controller.login(
            instruction, display=display,
            read_input=lambda prompt: read_auth_input(app, prompt),
        )
        if affects_current:
            reset_conversation(app)
            display("Active login updated; a new conversation has been opened.")
        display(result)
        app.input.replace("/logswitch ")
    except asyncio.CancelledError:
        display("Login cancelled.")
    except Exception:
        display("Login did not complete. Retry /login or check /logswitch for saved credentials.")
    finally:
        if getattr(app, "_auth_task", None) is asyncio.current_task():
            app._auth_task = None
        app.redraw()
