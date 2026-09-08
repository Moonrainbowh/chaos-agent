from __future__ import annotations

from .terminal_state import TerminalState


def reset_conversation(app: object) -> None:
    """Enter an empty conversation without modifying saved threads or tasks."""
    app.state = TerminalState()
    app.current_thread_id = None
    app.active_task_id = None
    app._flushed_entries = 0
