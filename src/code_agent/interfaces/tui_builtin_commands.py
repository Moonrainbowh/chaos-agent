from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from code_agent.checkpoints.models import (
    RewindError,
    RewindPreview,
    RewindRecoveryRequired,
)
from code_agent.sessions.models import CheckpointRecord
from code_agent.sessions.workspace_models import RewindOperationStatus

from .picker import PickerItem, PickerSource, PickerState
from .terminal_display import DisplayKind, clip_display, safe_text
from .terminal_state import TerminalState
from .tui_commands import TuiCommand, TuiCommandKind
from .tui_mcp_commands import handle_mcp_command
from .tui_skill_commands import handle_skill_command


async def handle_builtin_command(app: Any, command: TuiCommand) -> bool | None:
    if command.kind is TuiCommandKind.CLEAR:
        app.state = TerminalState(); app.current_thread_id = None; app._flushed_entries = 0
    elif command.kind is TuiCommandKind.EXIT:
        app.running = False
    elif command.kind is TuiCommandKind.NEW:
        app.state = TerminalState(); app.current_thread_id = None; app.active_task_id = None; app._flushed_entries = 0
    elif command.kind is TuiCommandKind.SESSIONS:
        records = await app.sessions.list_threads() if app.sessions else ()
        app._append(DisplayKind.METADATA, " | ".join(str(getattr(item, "id", item)) for item in records))
    elif command.kind is TuiCommandKind.RESTORE:
        if not command.instruction:
            app._append(DisplayKind.ERROR, "thread id is required")
            return False
        return await app.restore_thread(command.instruction)
    elif command.kind is TuiCommandKind.SKILL:
        return await handle_skill_command(app, command.instruction)
    elif command.kind is TuiCommandKind.MCP:
        return await handle_mcp_command(app, command.instruction)
    elif command.kind is TuiCommandKind.CHECKPOINT:
        return await _checkpoint_command(app, command.instruction)
    elif command.kind is TuiCommandKind.REWIND:
        return await _begin_rewind(app, command.instruction)
    else:
        return None
    return True


class _RewindStage(str, Enum):
    CHECKPOINT = "checkpoint"
    MODE = "mode"
    CONFIRM = "confirm"


@dataclass
class _RewindFlow:
    task_id: str
    records: tuple[CheckpointRecord, ...]
    stage: _RewindStage = _RewindStage.CHECKPOINT
    checkpoint: CheckpointRecord | None = None
    preview: RewindPreview | None = None
    picker: PickerState = field(default_factory=lambda: PickerState(limit=6))
    confirmation_choice: int = 0


async def _checkpoint_command(app: Any, instruction: str | None) -> bool:
    task_id = _task_id(app)
    if app.checkpoints is None or task_id is None:
        app._append(DisplayKind.ERROR, "checkpoint control is unavailable")
        return False
    action, _, argument = (instruction or "").partition(" ")
    if action.casefold() in {"list", "列表"}:
        records = await app.checkpoints.list(task_id)
        app._append(DisplayKind.METADATA, _checkpoint_list(records))
        return True
    if action.casefold() in {"create", "创建"}:
        record = await app.checkpoints.create(task_id, argument.strip() or "manual")
        app._append(DisplayKind.METADATA, f"checkpoint created · {record.id}")
        return True
    app._append(DisplayKind.ERROR, "checkpoint action is required")
    return False


async def _begin_rewind(app: Any, checkpoint_id: str | None) -> bool:
    task_id = _task_id(app)
    if app.checkpoints is None or task_id is None:
        app._append(DisplayKind.ERROR, "rewind control is unavailable")
        return False
    records = await app.checkpoints.list(task_id)
    if not records:
        app._append(DisplayKind.ERROR, "no checkpoints are available")
        return False
    flow = _RewindFlow(task_id, records)
    app._rewind_flow = flow
    if checkpoint_id is None:
        flow.picker.set_items(_checkpoint_items(records))
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
    if flow.stage is not _RewindStage.CONFIRM:
        return flow.picker.rows(app._columns())
    assert flow.preview is not None
    value = flow.preview
    heading = (
        f"restore {value.restore_count} · delete {value.delete_count} · "
        f"{value.total_bytes} bytes"
    )
    footer = (
        ("› " if flow.confirmation_choice == 0 else "  ") + "No",
        ("› " if flow.confirmation_choice == 1 else "  ") + "Yes",
        "Enter select · Esc cancel",
    )
    return _bounded_preview_rows(
        heading, value.paths, footer, app._columns()
    )


def _bounded_preview_rows(
    heading: str, paths: tuple[str, ...], footer: tuple[str, ...], width: int
) -> tuple[str, ...]:
    shown = paths[:5]
    suffix = (f"… {len(paths) - len(shown)} more",) if len(paths) > len(shown) else ()
    fixed = tuple(_clip_row(row, width, 16_384) for row in (heading, *suffix, *footer))
    remaining = 16_384 - sum(len(row.encode("utf-8")) for row in fixed)
    path_rows = []
    for path in shown:
        row = _clip_row(path, width, remaining)
        if not row:
            break
        path_rows.append(row)
        remaining -= len(row.encode("utf-8"))
    return (fixed[0], *path_rows, *fixed[1:])


def _clip_row(value: str, width: int, byte_limit: int) -> str:
    clipped = clip_display(safe_text(value), width)
    if len(clipped.encode("utf-8")) <= byte_limit:
        return clipped
    return clipped.encode("utf-8")[:byte_limit].decode("utf-8", "ignore")


async def handle_rewind_key(app: Any, key: str) -> bool:
    flow = getattr(app, "_rewind_flow", None)
    if flow is None:
        return False
    if key == "\x1b":
        _cancel_rewind(app)
        return True
    if flow.stage is _RewindStage.CONFIRM:
        return await _handle_confirmation(app, flow, key)
    if key in {"up", "left"}:
        flow.picker.move(-1)
    elif key in {"down", "right"}:
        flow.picker.move(1)
    elif key in {"\r", "\n"}:
        await _accept_rewind_picker(app, flow)
    return True


async def _accept_rewind_picker(app: Any, flow: _RewindFlow) -> None:
    selection = flow.picker.accept()
    if selection is None:
        return
    if flow.stage is _RewindStage.CHECKPOINT:
        selected = next(
            item for item in flow.records if item.id == selection.item.identifier
        )
        _choose_checkpoint(flow, selected)
        return
    assert flow.checkpoint is not None
    try:
        flow.preview = await app.checkpoints.preview_rewind(
            flow.task_id, flow.checkpoint.id, selection.item.identifier
        )
    except (RewindError, ValueError) as error:
        app._rewind_flow = None
        app._append(DisplayKind.ERROR, str(error))
        return
    flow.stage = _RewindStage.CONFIRM
    flow.confirmation_choice = 0


async def _handle_confirmation(app: Any, flow: _RewindFlow, key: str) -> bool:
    if key in {"left", "up"}:
        flow.confirmation_choice = 0
    elif key in {"right", "down"}:
        flow.confirmation_choice = 1
    elif key in {"\r", "\n"}:
        if flow.confirmation_choice == 0:
            _cancel_rewind(app)
        else:
            await _execute_rewind(app, flow)
    return True


async def _execute_rewind(app: Any, flow: _RewindFlow) -> None:
    assert flow.preview is not None
    app._append(DisplayKind.METADATA, "rewind in progress")
    try:
        result = await app.checkpoints.execute_rewind(flow.preview, confirmed=True)
    except RewindRecoveryRequired as error:
        app._append(DisplayKind.ERROR, f"recovery-required · {error}")
        app._rewind_flow = None
        return
    except RewindError as error:
        app._append(DisplayKind.ERROR, f"rewind failed · {error}")
        app._rewind_flow = None
        return
    app._rewind_flow = None
    if result.replacement_task_id is not None:
        app.active_task_id = result.replacement_task_id
    kind = (
        DisplayKind.SUCCESS
        if result.status is RewindOperationStatus.COMPLETED
        else DisplayKind.ERROR
    )
    app._append(kind, f"rewind {result.status.value} · {result.operation_id}")


def _choose_checkpoint(flow: _RewindFlow, record: CheckpointRecord) -> None:
    flow.checkpoint = record
    flow.stage = _RewindStage.MODE
    flow.picker.set_items(_mode_items(_code_available(record)))


def _checkpoint_items(
    records: tuple[CheckpointRecord, ...],
) -> tuple[PickerItem, ...]:
    return tuple(
        PickerItem(
            record.id,
            record.label,
            PickerSource.SESSION,
            "code available" if _code_available(record) else "code unavailable",
            (record.id,),
        )
        for record in records
    )


def _mode_items(code_available: bool) -> tuple[PickerItem, ...]:
    reason = None if code_available else "checkpoint has no recoverable code"
    return (
        PickerItem("code", "仅代码", PickerSource.MODE, enabled=code_available, disabled_reason=reason),
        PickerItem("session", "仅会话", PickerSource.MODE),
        PickerItem("code_and_session", "代码与会话", PickerSource.MODE, enabled=code_available, disabled_reason=reason),
    )


def _checkpoint_list(records: tuple[CheckpointRecord, ...]) -> str:
    if not records:
        return "no checkpoints"
    rows = [
        f"{item.id} · {item.label} · "
        f"{'code available' if _code_available(item) else 'code unavailable'}"
        for item in records[:20]
    ]
    if len(records) > 20:
        rows.append(f"… {len(records) - 20} more")
    return "\n".join(rows)


def _code_available(record: CheckpointRecord) -> bool:
    digest = record.metadata.get("inventory_digest")
    return isinstance(digest, str) and len(digest) == 64


def _task_id(app: Any) -> str | None:
    return app.active_task_id or app.state.task_id


def _cancel_rewind(app: Any) -> None:
    app._rewind_flow = None
    app._append(DisplayKind.METADATA, "rewind cancelled")
