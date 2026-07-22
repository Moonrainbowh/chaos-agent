from __future__ import annotations

from typing import Any

from .checkpoint_commands import handle_checkpoint_command
from .checkpoint_tui import begin_rewind
from .terminal_display import DisplayKind
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
        return await handle_checkpoint_command(
            app, command.action, command.instruction
        )
    elif command.kind is TuiCommandKind.REWIND:
        return await begin_rewind(app, command.instruction)
    else:
        return None
    return True
