from __future__ import annotations

from typing import Any

from .terminal_display import DisplayKind
from .terminal_state import TerminalState
from .terminal_status import status_snapshot
from .tui_commands import TuiCommand, TuiCommandKind


async def handle_builtin_command(app: Any, command: TuiCommand) -> bool | None:
    if command.kind is TuiCommandKind.CLEAR:
        app.state = TerminalState(); app.current_thread_id = None; app._flushed_entries = 0
    elif command.kind is TuiCommandKind.EXIT:
        app.running = False
    elif command.kind is TuiCommandKind.DIAG:
        app._append(DisplayKind.METADATA, status_snapshot(app.state.status, app.active_task_id or app.state.task_id, app.current_thread_id, app.profiles.current.model if app.profiles else None))
    elif command.kind is TuiCommandKind.TRACE:
        app._append(DisplayKind.METADATA, " | ".join(app.state.timeline[-10:]) or "no trace available")
    elif command.kind is TuiCommandKind.NEW:
        app.state = TerminalState(); app.current_thread_id = None; app.active_task_id = None; app._flushed_entries = 0
    elif command.kind is TuiCommandKind.SESSIONS:
        records = await app.sessions.list_threads() if app.sessions else ()
        app._append(DisplayKind.METADATA, " | ".join(str(getattr(item, "id", item)) for item in records))
    elif command.kind in {TuiCommandKind.OPEN, TuiCommandKind.RESTORE}:
        if not command.instruction:
            app._append(DisplayKind.ERROR, "thread id is required")
            return False
        return await app.restore_thread(command.instruction)
    else:
        return None
    return True
