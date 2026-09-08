from __future__ import annotations

import asyncio
import time

from code_agent.core.cancellation import CancellationToken

from .attachment_input import has_submission_input, prepare_input
from .command_availability import available_services
from .terminal_display import DisplayKind
from .tui_commands import parse_tui_command
from .tui_peer_turn import yield_peer_slot
from .tui_submission import submit_active_input


async def submit(app: object, text: str, attachments: object = None) -> bool:
    """Accept input without holding the keyboard loop during workspace creation."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not has_submission_input(app.attachment_draft, text, attachments):
        return False
    if app._pending_approval is not None:
        return _reject(app, text, "approval decision is pending")
    if not await yield_peer_slot(app):
        return False
    parsed = parse_tui_command(text, available_services(app), app.command_registry)
    if parsed.is_command:
        try:
            accepted = await app._handle_command(parsed)
        except (KeyError, OSError, RuntimeError, ValueError) as error:
            return _reject(app, text, str(error))
        if not accepted and not app.input.text:
            app.input.replace(text)
        app.redraw()
        return accepted
    if parsed.error:
        if _is_direct_skill_invocation(app, text):
            return await _handle_direct_skill(app, text, attachments)
        return _reject(app, text, parsed.error)
    if getattr(app, "_starting_task", False):
        return _reject(app, text, "Preparing workspace. Input kept; retry when preparation finishes.")
    try:
        prepared = prepare_input(app.attachment_draft, text, attachments)
    except (RuntimeError, ValueError) as error:
        return _reject(app, text, str(error))
    app._append(DisplayKind.USER, prepared.display)
    if app._run_task and not app._run_task.done():
        if app.tasks and app.active_task_id:
            return await submit_active_input(app, prepared)
        return _reject(app, text, "A response is running. Pause it before sending another task.")
    return await start_prepared(app, prepared, text)


async def start_prepared(app: object, prepared: object, original: str = "") -> bool:
    app._token = CancellationToken()
    app._run_started_at = time.monotonic()
    app._task_finished_handled = False
    app.state.begin_run()
    app._starting_task = bool(app.tasks and not app.active_task_id)
    if app._starting_task:
        app.state.status = "preparing_workspace"
    runner = asyncio.create_task(_run(app, prepared, original))
    app._run_task = runner
    runner.add_done_callback(lambda finished: finish_run(app, finished))
    app.update_terminal_title(running=True)
    app._start_animation()
    app.redraw()
    await asyncio.sleep(0)
    return runner.result() if runner.done() and not runner.cancelled() else True


async def _run(app: object, prepared: object, original: str) -> bool:
    try:
        if app.tasks:
            if app._starting_task:
                record = await app.tasks.start(prepared.prompt)
                app.active_task_id = record.id
                app._token.raise_if_cancelled()
            app._starting_task = False
            await app._consume_task(
                app.active_task_id, prepared.prompt, prepared.attachments, prepared
            )
        else:
            await app._consume(
                prepared.prompt, app._token, prepared.attachments, prepared
            )
        return True
    except asyncio.CancelledError:
        app.state.status = "paused"
        if not app.input.text:
            app.input.replace(original)
        return False
    except Exception as error:
        app.state.status = "error"
        return _reject(app, original, f"{type(error).__name__}: {error}")
    finally:
        app._starting_task = False


def finish_run(app: object, runner: asyncio.Task) -> None:
    """Redraw after the runner is done, removing queue and pause hints."""
    if app._run_task is not runner or not runner.done():
        return
    app.state.last_rate = app.state.token_rate.rate()
    app._run_started_at = None
    app._token = None
    if not app._closing:
        app.on_task_finished()
        app._request_redraw(immediate=True)


def _is_direct_skill_invocation(app: object, text: str) -> bool:
    if not text.startswith(("/", ":")):
        return False
    skills = getattr(app, "skills", None)
    if skills is None:
        return False
    raw = text[1:].lstrip()
    skill_id, _, _ = raw.partition(" ")
    skill_id = skill_id.strip()
    if not skill_id:
        return False
    try:
        skills.info(skill_id)
        return True
    except Exception:
        return False


async def _handle_direct_skill(app: object, text: str, attachments: object = None) -> bool:
    raw = text[1:].lstrip()
    skill_id, _, prompt = raw.partition(" ")
    skill_id = skill_id.strip()
    prompt = prompt.strip()
    thread_id = getattr(app, "current_thread_id", None)
    if thread_id is not None and hasattr(app.skills, "enable"):
        try:
            await app.skills.enable(thread_id, skill_id)
        except Exception as error:
            app._append(DisplayKind.ERROR, f"Skill activation failed: {error}")
            return False
    skill = app.skills.info(skill_id)
    desc = (getattr(skill, "description", "") or "").split("\n")[0]
    if prompt:
        app._append(DisplayKind.METADATA, f"Skill [{skill_id}] active · {desc}")
        try:
            prepared = prepare_input(app.attachment_draft, prompt, attachments)
        except (RuntimeError, ValueError) as error:
            return _reject(app, prompt, str(error))
        app._append(DisplayKind.USER, prepared.display)
        if app._run_task and not app._run_task.done():
            if app.tasks and app.active_task_id:
                return await submit_active_input(app, prepared)
            return _reject(app, prompt, "A response is running. Pause it before sending another task.")
        return await start_prepared(app, prepared, prompt)
    else:
        app._append(
            DisplayKind.METADATA,
            f"Skill [{skill_id}] active · {desc}\n  Ready. Run /{skill_id} <instruction> or enter your prompt directly."
        )
        app.redraw()
        return True


def _reject(app: object, original: str, reason: str) -> bool:
    app._append(DisplayKind.ERROR, reason)
    if not app.input.text:
        app.input.replace(original)
    app.redraw()
    return False
