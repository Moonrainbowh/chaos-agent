from __future__ import annotations

from .terminal_state import TerminalState
from code_agent.core.debug_trace import trace_event


def reset_conversation(app: object) -> None:
    """Enter an empty conversation without modifying saved threads or tasks."""
    previous_thread_id = getattr(app, "current_thread_id", None)
    previous_entries = len(getattr(getattr(app, "state", None), "entries", ()))
    if getattr(app, "_tail_geometry", None) is not None:
        app._write(app._tail_clear_sequence())
    app.state = TerminalState()
    app.current_thread_id = None
    app.active_task_id = None
    app._pending_skill_id = None
    app._pending_skill_ids = None
    app._flushed_entries = 0
    app._tail_geometry = None
    app._projection_epoch = getattr(app, "_projection_epoch", 0) + 1
    app._drawn_draft_revision = -1
    app._drawn_size = None
    app._write("\x1b[3J\x1b[2J\x1b[H")
    trace_event(
        "tui.thread",
        "reset",
        previous_thread_id=previous_thread_id,
        projection_epoch=app._projection_epoch,
        entries_cleared=previous_entries,
    )
