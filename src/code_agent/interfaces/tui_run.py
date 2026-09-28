from __future__ import annotations

import asyncio
import time

from code_agent.core.cancellation import CancellationError, CancellationToken

from .attachment_input import has_submission_input, prepare_input
from .command_availability import available_services
from .terminal_display import DisplayKind
from .tui_commands import parse_tui_command
from .tui_peer_turn import yield_peer_slot
from .tui_submission import submit_active_input
from .tui_auth_prompt import auth_active
from code_agent.skills.matcher import match_skill
from code_agent.skills.requests import resolve_skill_request


async def submit(
    app: object,
    text: str,
    attachments: object = None,
    *,
    skill_id: str | None = None,
    skill_ids: tuple[str, ...] | None = None,
    _skill_prompt: bool = False,
) -> bool:
    """Accept input without holding the keyboard loop during workspace creation."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if skill_ids is None and skill_id is None:
        skill_id = getattr(app, "_pending_skill_id", None)
        skill_ids = getattr(app, "_pending_skill_ids", None)
    selected = skill_ids or ((skill_id,) if skill_id else ())
    if auth_active(app):
        return False
    if not has_submission_input(app.attachment_draft, text, attachments):
        return False
    if app._pending_approval is not None:
        return _reject(app, text, "approval decision is pending")
    if not await yield_peer_slot(app):
        return False
    if not _skill_prompt:
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
    if selected:
        app._pending_skill_id = None
        app._pending_skill_ids = None
    if not selected and hasattr(app.skills, "list") and (
        getattr(app, "tasks", None) is not None
        or getattr(app, "current_thread_id", None) is not None
    ):
        thread_id = getattr(app, "current_thread_id", None)
        restore = getattr(app.skills, "restore", None)
        if thread_id is not None and callable(restore):
            try:
                await restore(thread_id)
            except Exception as error:
                return _reject(app, text, f"Skill restore failed: {error}")
        automatic = match_skill(text, app.skills.list())
        if automatic is not None:
            if not _skill_is_active(app, automatic.identifier):
                skill_id = automatic.identifier
                selected = (skill_id,)
            app._append(
                DisplayKind.METADATA,
                f"Auto Skill [{automatic.identifier}] matched · {', '.join(automatic.matched_terms)}",
            )
    if getattr(app, "_starting_task", False):
        return _reject(app, text, "Preparing workspace. Input kept; retry when preparation finishes.")
    try:
        prepared = prepare_input(app.attachment_draft, text, attachments)
    except (RuntimeError, ValueError) as error:
        return _reject(app, text, str(error))
    if app._run_task and not app._run_task.done():
        if app.tasks and app.active_task_id:
            if selected and hasattr(app.skills, "enable"):
                try:
                    record = await app.sessions.load_task(app.active_task_id)
                    restore = getattr(app.skills, "restore", None)
                    if callable(restore):
                        await restore(record.thread_id)
                    await _enable_skills(app, record.thread_id, selected)
                    _append_skill_activation(app, selected)
                except Exception as error:
                    return _reject(app, text, f"Skill activation failed: {error}")
            app._append(DisplayKind.USER, prepared.display)
            return await submit_active_input(app, prepared)
        app._append(DisplayKind.USER, prepared.display)
        return _reject(app, text, "A response is running. Pause it before sending another task.")
    if not selected:
        app._append(DisplayKind.USER, prepared.display)
    return await start_prepared(app, prepared, text, skill_id=skill_id, skill_ids=selected or None)


async def start_prepared(
    app: object,
    prepared: object,
    original: str = "",
    *,
    skill_id: str | None = None,
    skill_ids: tuple[str, ...] | None = None,
) -> bool:
    app._token = CancellationToken()
    app._run_started_at = time.monotonic()
    app._task_finished_handled = False
    # Only a paused/interrupted task is a retry. A completed or waiting task
    # followed by fresh input must not project its previous plan into this run.
    resumable = getattr(app.state, "task_status", None) in {"paused", "interrupted"}
    app.state.begin_run(
        preserve_plan=bool(app.tasks and app.active_task_id and resumable)
    )
    app._starting_task = bool(app.tasks and not app.active_task_id)
    if app._starting_task:
        app.state.status = "preparing_workspace"
    runner = asyncio.create_task(_run(app, prepared, original, skill_id, skill_ids))
    app._run_task = runner
    runner.add_done_callback(lambda finished: finish_run(app, finished))
    app.update_terminal_title(running=True)
    app._start_animation()
    app.redraw()
    await asyncio.sleep(0)
    return runner.result() if runner.done() and not runner.cancelled() else True


async def _run(
    app: object,
    prepared: object,
    original: str,
    skill_id: str | None = None,
    skill_ids: tuple[str, ...] | None = None,
) -> bool:
    selected = skill_ids or ((skill_id,) if skill_id else ())
    try:
        if app.tasks:
            starting_task = app._starting_task
            if app._starting_task:
                record = await app.tasks.start(prepared.prompt)
                app.active_task_id = record.id
                if selected:
                    try:
                        restore = getattr(app.skills, "restore", None)
                        if callable(restore):
                            await restore(record.thread_id)
                        await _enable_skills(app, record.thread_id, selected)
                        _append_skill_activation(app, selected)
                    except asyncio.CancelledError:
                        await app.tasks.stop(record.id)
                        app.active_task_id = None
                        raise
                    except Exception as error:
                        try:
                            await app.tasks.stop(record.id)
                        finally:
                            app.state.status = "error"
                            app.active_task_id = None
                        app._append(DisplayKind.ERROR, f"Skill activation failed: {error}")
                        if not app.input.text:
                            app.input.replace(original)
                        return False
                try:
                    app._token.raise_if_cancelled()
                except CancellationError:
                    await app.tasks.stop(record.id)
                    app.active_task_id = None
                    app.state.status = "paused"
                    return False
            app._starting_task = False
            if selected and not starting_task and hasattr(app.skills, "enable"):
                try:
                    record = await app.sessions.load_task(app.active_task_id)
                    restore = getattr(app.skills, "restore", None)
                    if callable(restore):
                        await restore(record.thread_id)
                    await _enable_skills(app, record.thread_id, selected)
                    _append_skill_activation(app, selected)
                except Exception as error:
                    app._append(DisplayKind.ERROR, f"Skill activation failed: {error}")
                    app.state.status = "error"
                    if not app.input.text:
                        app.input.replace(original)
                    return False
            if selected:
                app._append(DisplayKind.USER, prepared.display)
            await app._consume_task(
                app.active_task_id, prepared.prompt, prepared.attachments, prepared
            )
        else:
            if selected and hasattr(app.skills, "enable"):
                thread_id = getattr(app, "current_thread_id", None)
                if thread_id is None:
                    raise RuntimeError("Skill activation requires a thread")
                restore = getattr(app.skills, "restore", None)
                if callable(restore):
                    await restore(thread_id)
                await _enable_skills(app, thread_id, selected)
                _append_skill_activation(app, selected)
            if selected:
                app._append(DisplayKind.USER, prepared.display)
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
    app.state.finish_run(app._run_started_at, time.monotonic())
    app._run_started_at = None
    app._token = None
    if not app._closing:
        app.on_task_finished()
        app._request_redraw(immediate=True)


def _is_direct_skill_invocation(app: object, text: str) -> bool:
    skills = getattr(app, "skills", None)
    return skills is not None and resolve_skill_request(text, skills) is not None


def _skill_is_active(app: object, identifier: str) -> bool:
    if getattr(app, "tasks", None) is not None and getattr(app, "active_task_id", None) is None:
        return False
    thread_id = getattr(app, "current_thread_id", None)
    activation = getattr(getattr(app, "skills", None), "activation", None)
    if thread_id is None or not callable(activation):
        return False
    try:
        return any(item.identifier == identifier for item in activation(thread_id).active())
    except (KeyError, LookupError, AttributeError):
        return False


def _append_skill_activation(app: object, skill_ids: tuple[str, ...]) -> None:
    for identifier in skill_ids:
        app._append(DisplayKind.METADATA, f"Skill [{identifier}] active")


async def _enable_skills(app: object, thread_id: str, skill_ids: tuple[str, ...]) -> None:
    activation = getattr(app.skills, "activation", None)
    existing = set()
    if callable(activation):
        existing = {item.identifier for item in activation(thread_id).active()}
    activated: list[str] = []
    attempting: str | None = None
    try:
        for identifier in skill_ids:
            if identifier in existing:
                continue
            attempting = identifier
            await app.skills.enable(thread_id, identifier)
            activated.append(identifier)
            attempting = None
    except (Exception, asyncio.CancelledError) as error:
        rollback_errors: list[Exception] = []
        rollback_ids = list(reversed(activated))
        if attempting is not None:
            rollback_ids.insert(0, attempting)
        for identifier in rollback_ids:
            try:
                await app.skills.disable(thread_id, identifier)
            except Exception as rollback_error:
                rollback_errors.append(rollback_error)
        if rollback_errors:
            raise RuntimeError("Skill activation rollback failed") from error
        raise


async def _handle_direct_skill(app: object, text: str, attachments: object = None) -> bool:
    request = resolve_skill_request(text, app.skills)
    if request is None:
        return False
    prompt = request.prompt
    skill_id = request.skill_ids[0]
    skill = app.skills.info(skill_id)
    desc = (getattr(skill, "description", "") or "").split("\n")[0]
    if prompt:
        app._append(DisplayKind.METADATA, f"Skill [{skill_id}] requested · {desc}")
        return await submit(app, prompt, attachments, skill_ids=request.skill_ids, _skill_prompt=True)
    else:
        app._pending_skill_id = skill_id
        app._pending_skill_ids = request.skill_ids
        app._append(
            DisplayKind.METADATA,
            f"Skill [{skill_id}] selected · {desc}\n  Ready. Run /{skill_id} <instruction> or enter your prompt directly."
        )
        app.redraw()
        return True


def _reject(app: object, original: str, reason: str) -> bool:
    app._append(DisplayKind.ERROR, reason)
    if not app.input.text:
        app.input.replace(original)
    app.redraw()
    return False
