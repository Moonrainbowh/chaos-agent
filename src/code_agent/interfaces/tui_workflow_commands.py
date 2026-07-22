from __future__ import annotations

from typing import Any

from .terminal_display import DisplayKind
from .workflow_view import WorkflowView


async def handle_workflow_command(app: Any, instruction: str | None) -> bool:
    task_id = app.active_task_id or app.state.task_id
    if app.workflows is None or not task_id:
        app._append(DisplayKind.ERROR, "workflow is unavailable")
        return False
    snapshot = await app.workflows.load_workflow_for_task(task_id)
    if snapshot is None:
        app._append(DisplayKind.ERROR, "workflow is unavailable")
        return False
    view = WorkflowView()
    value = (instruction or "").strip()
    try:
        if value.startswith("证据 ") or value.startswith("evidence "):
            app._append(
                DisplayKind.METADATA,
                view.evidence(snapshot, value.split(" ", 1)[1], width=app._columns()),
            )
        elif value and value not in {"失败", "failed"}:
            app._append(
                DisplayKind.METADATA,
                view.detail(snapshot, value, width=app._columns()),
            )
        else:
            app._append(
                DisplayKind.METADATA,
                view.render(
                    snapshot,
                    width=app._columns(),
                    filter_name=value or None,
                ),
            )
    except (KeyError, ValueError):
        app._append(DisplayKind.ERROR, "workflow node is unavailable")
        return False
    return True
