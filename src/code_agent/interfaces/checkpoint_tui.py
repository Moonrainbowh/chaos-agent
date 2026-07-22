from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from code_agent.checkpoints.models import (
    RewindPreview,
    RewindRecoveryRequired,
)
from code_agent.sessions.models import CheckpointRecord
from code_agent.sessions.workspace_models import RewindOperationStatus

from .checkpoint_commands import (
    append_control_error,
    checkpoint_picker_items,
    code_available,
    current_task_id,
)
from .picker import PickerItem, PickerSource, PickerState
from .terminal_display import DisplayKind
from .checkpoint_tui_render import render_rewind_rows


class RewindStage(str, Enum):
    CHECKPOINT = "checkpoint"
    MODE = "mode"
    PREVIEWING = "previewing"
    CONFIRM = "confirm"
    EXECUTING = "executing"


@dataclass
class RewindFlow:
    task_id: str
    records: tuple[CheckpointRecord, ...]
    stage: RewindStage = RewindStage.CHECKPOINT
    checkpoint: CheckpointRecord | None = None
    mode: str | None = None
    preview: RewindPreview | None = None
    picker: PickerState = field(default_factory=lambda: PickerState(limit=6))
    confirmation_choice: int = 0
    cancel_notice: bool = False


async def begin_rewind(app: Any, checkpoint_id: str | None) -> bool:
    task_id = current_task_id(app)
    if app.checkpoints is None or task_id is None:
        app._append(DisplayKind.ERROR, "rewind control is unavailable")
        return False
    if _busy(app):
        app._append(DisplayKind.ERROR, "rewind is already in progress")
        return False
    try:
        records = await app.checkpoints.list(task_id)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        app._rewind_flow = None
        append_control_error(app, "rewind failed", error)
        return False
    if not records:
        app._append(DisplayKind.ERROR, "no checkpoints are available")
        return False
    flow = RewindFlow(task_id, records)
    app._rewind_flow = flow
    if checkpoint_id is None:
        flow.picker.set_items(checkpoint_picker_items(records))
        return True
    selected = next((item for item in records if item.id == checkpoint_id), None)
    if selected is None:
        app._rewind_flow = None
        app._append(DisplayKind.ERROR, "checkpoint was not found")
        return False
    _choose_checkpoint(flow, selected)
    return True


def rewind_rows(app: Any) -> tuple[str, ...] | None:
    flow = getattr(app, "_rewind_flow", None)
    if flow is None:
        return None
    return render_rewind_rows(flow, app._columns())


async def handle_rewind_key(app: Any, key: str) -> bool:
    flow = getattr(app, "_rewind_flow", None)
    if flow is None:
        return False
    if flow.stage in {RewindStage.PREVIEWING, RewindStage.EXECUTING}:
        _handle_busy_key(app, flow, key)
        return True
    if key == "\x1b":
        _cancel_flow(app)
    elif flow.stage is RewindStage.CONFIRM:
        _handle_confirmation(app, flow, key)
    elif key in {"up", "left"}:
        flow.picker.move(-1)
    elif key in {"down", "right"}:
        flow.picker.move(1)
    elif key in {"\r", "\n"}:
        _accept_picker(app, flow)
    return True


def _accept_picker(app: Any, flow: RewindFlow) -> None:
    selection = flow.picker.accept()
    if selection is None:
        return
    if flow.stage is RewindStage.CHECKPOINT:
        selected = next(
            item for item in flow.records if item.id == selection.item.identifier
        )
        _choose_checkpoint(flow, selected)
        return
    assert flow.checkpoint is not None
    flow.mode = selection.item.identifier
    flow.stage = RewindStage.PREVIEWING
    _start_task(app, _run_preview(app, flow))


async def _run_preview(app: Any, flow: RewindFlow) -> None:
    assert flow.checkpoint is not None and flow.mode is not None
    try:
        result = await app.checkpoints.preview_rewind(
            flow.task_id, flow.checkpoint.id, flow.mode
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        if app._rewind_flow is flow:
            app._rewind_flow = None
            append_control_error(app, "rewind preview failed", error)
    else:
        if app._rewind_flow is flow:
            flow.preview = result
            flow.stage = RewindStage.CONFIRM
            flow.confirmation_choice = 0


def _handle_confirmation(app: Any, flow: RewindFlow, key: str) -> None:
    if key in {"left", "up"}:
        flow.confirmation_choice = 0
    elif key in {"right", "down"}:
        flow.confirmation_choice = 1
    elif key in {"\r", "\n"}:
        if flow.confirmation_choice == 0:
            _cancel_flow(app)
            return
        flow.stage = RewindStage.EXECUTING
        app._append(DisplayKind.METADATA, "rewind in progress")
        _start_task(app, _run_execute(app, flow))


async def _run_execute(app: Any, flow: RewindFlow) -> None:
    assert flow.preview is not None
    try:
        result = await app.checkpoints.execute_rewind(flow.preview, confirmed=True)
    except asyncio.CancelledError:
        raise
    except RewindRecoveryRequired as error:
        app._append(DisplayKind.ERROR, f"recovery-required · {error}")
        app._rewind_flow = None
        return
    except Exception as error:
        append_control_error(app, "rewind failed", error)
        app._rewind_flow = None
        return
    app._rewind_flow = None
    if (
        result.status is RewindOperationStatus.COMPLETED
        and result.replacement_task_id is not None
    ):
        app.active_task_id = result.replacement_task_id
    kind = (
        DisplayKind.SUCCESS
        if result.status is RewindOperationStatus.COMPLETED
        else DisplayKind.ERROR
    )
    app._append(kind, f"rewind {result.status.value} · {result.operation_id}")


def _start_task(app: Any, coroutine: object) -> bool:
    if _busy(app):
        coroutine.close()
        return False
    task = asyncio.create_task(coroutine)
    app._rewind_task = task
    task.add_done_callback(lambda completed: _task_done(app, completed))
    return True


def _task_done(app: Any, task: asyncio.Task[None]) -> None:
    if app._rewind_task is task:
        app._rewind_task = None
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as error:
        app._rewind_flow = None
        append_control_error(app, "rewind failed", error)
    if not getattr(app, "_closing", False):
        app._request_redraw(immediate=True)


async def wait_rewind_task(app: Any) -> None:
    task = getattr(app, "_rewind_task", None)
    if task is not None:
        await asyncio.gather(task, return_exceptions=True)


async def close_rewind_flow(app: Any) -> None:
    task = getattr(app, "_rewind_task", None)
    flow = getattr(app, "_rewind_flow", None)
    if task is None:
        return
    if flow is not None and flow.stage is RewindStage.PREVIEWING:
        app._rewind_flow = None
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    else:
        await _await_durable_task(task)
    if app._rewind_task is task:
        app._rewind_task = None


async def _await_durable_task(task: asyncio.Task[None]) -> None:
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                break
    await asyncio.gather(task, return_exceptions=True)


def _handle_busy_key(app: Any, flow: RewindFlow, key: str) -> None:
    if key != "\x1b":
        return
    if flow.stage is RewindStage.PREVIEWING:
        app._rewind_flow = None
        if app._rewind_task is not None:
            app._rewind_task.cancel()
        app._append(DisplayKind.METADATA, "rewind preview cancelled")
    elif not flow.cancel_notice:
        flow.cancel_notice = True
        app._append(DisplayKind.METADATA, "rewind execution is durable; waiting")


def _choose_checkpoint(flow: RewindFlow, record: CheckpointRecord) -> None:
    flow.checkpoint = record
    flow.stage = RewindStage.MODE
    flow.picker.set_items(_mode_items(code_available(record)))


def _mode_items(available: bool) -> tuple[PickerItem, ...]:
    reason = None if available else "checkpoint has no recoverable code"
    return (
        PickerItem("code", "仅代码", PickerSource.MODE, enabled=available, disabled_reason=reason),
        PickerItem("session", "仅会话", PickerSource.MODE),
        PickerItem("code_and_session", "代码与会话", PickerSource.MODE, enabled=available, disabled_reason=reason),
    )


def _busy(app: Any) -> bool:
    task = getattr(app, "_rewind_task", None)
    return task is not None and not task.done()


def _cancel_flow(app: Any) -> None:
    app._rewind_flow = None
    app._append(DisplayKind.METADATA, "rewind cancelled")
