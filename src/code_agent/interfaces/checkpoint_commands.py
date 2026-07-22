from __future__ import annotations

import asyncio
from typing import Any

from code_agent.sessions.models import CheckpointRecord

from .picker import PickerItem, PickerSource
from .terminal_display import DisplayKind, clip_display, safe_text


async def handle_checkpoint_command(
    app: Any, action: str | None, instruction: str | None
) -> bool:
    task_id = current_task_id(app)
    if app.checkpoints is None or task_id is None:
        app._append(DisplayKind.ERROR, "checkpoint control is unavailable")
        return False
    argument = (instruction or "").partition(" ")[2].strip()
    try:
        if action == "列表":
            app._append(
                DisplayKind.METADATA,
                checkpoint_list(await app.checkpoints.list(task_id)),
            )
            return True
        if action == "创建":
            created = await app.checkpoints.create(task_id, argument or "manual")
            app._append(DisplayKind.METADATA, f"checkpoint created · {created.id}")
            return True
    except asyncio.CancelledError:
        raise
    except Exception as error:
        append_control_error(app, "checkpoint failed", error)
        return False
    app._append(DisplayKind.ERROR, "checkpoint action is required")
    return False


def checkpoint_list(records: tuple[CheckpointRecord, ...]) -> str:
    if not records:
        return "no checkpoints"
    rows = [
        clip_display(
            f"{item.id} · {safe_label(item.label)} · "
            f"{'code available' if code_available(item) else 'code unavailable'}",
            512,
        )
        for item in records[:20]
    ]
    if len(records) > 20:
        rows.append(f"… {len(records) - 20} more")
    return "\n".join(rows)


def checkpoint_picker_items(
    records: tuple[CheckpointRecord, ...],
) -> tuple[PickerItem, ...]:
    return tuple(
        PickerItem(
            item.id,
            safe_label(item.label),
            PickerSource.SESSION,
            "code available" if code_available(item) else "code unavailable",
            (item.id,),
        )
        for item in records
    )


def code_available(record: CheckpointRecord) -> bool:
    digest = record.metadata.get("inventory_digest")
    return isinstance(digest, str) and len(digest) == 64


def safe_label(value: str) -> str:
    return clip_display(safe_text(value), 160)


def append_control_error(app: Any, prefix: str, error: Exception) -> None:
    detail = clip_display(safe_text(str(error) or type(error).__name__), 160)
    app._append(DisplayKind.ERROR, f"{prefix} · {detail}")


def current_task_id(app: Any) -> str | None:
    return app.active_task_id or app.state.task_id
