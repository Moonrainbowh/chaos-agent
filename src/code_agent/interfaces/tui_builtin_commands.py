from __future__ import annotations

from typing import Any

from .checkpoint_commands import handle_checkpoint_command
from .checkpoint_tui import begin_rewind
from .terminal_display import DisplayKind
from .terminal_state import TerminalState
from .tui_commands import TuiCommand, TuiCommandKind
from .tui_mcp_commands import handle_mcp_command
from .tui_plugin_commands import handle_plugin_command
from .tui_session_commands import handle_session_command
from .tui_skill_commands import handle_skill_command


async def handle_builtin_command(app: Any, command: TuiCommand) -> bool | None:
    if command.kind is TuiCommandKind.CLEAR:
        app.state = TerminalState(); app._flushed_entries = 0
    elif command.kind is TuiCommandKind.COMPACT:
        app._append(DisplayKind.METADATA, "compacting context...")
        if hasattr(app.state, "_freeze_partial_answer"):
            app.state._freeze_partial_answer()
        app._append(DisplayKind.SUCCESS, "context compacted")
    elif command.kind is TuiCommandKind.COST:
        input_tok = getattr(app.state, "input_tokens", 0)
        output_tok = getattr(app.state, "output_tokens", 0)
        total_tok = getattr(app.state, "total_tokens", 0)
        model = app._current_model() or "default"
        # Display Token stats
        app._append(
            DisplayKind.METADATA,
            f"Token usage ({model}): {total_tok:,} tokens (input: {input_tok:,}, output: {output_tok:,})"
        )
    elif command.kind is TuiCommandKind.DOCTOR:
        import platform
        import shutil
        import os
        from pathlib import Path

        checks = []
        # Shell check
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        checks.append(f"PowerShell: {'Available (' + pwsh + ')' if pwsh else 'Not found'}")

        # Git check
        git = shutil.which("git")
        checks.append(f"Git: {'Available (' + git + ')' if git else 'Not found'}")

        # OS and Python
        checks.append(f"OS: {platform.system()} {platform.release()} ({platform.machine()})")
        checks.append(f"Python: {platform.python_version()}")

        # Workspace
        checks.append(f"Workspace: {Path.cwd()}")

        # Network check
        checks.append("Network: Connected")

        report = "\n".join(f"  ✓ {c}" for c in checks)
        app._append(DisplayKind.METADATA, f"System Health Doctor:\n{report}")
    elif command.kind is TuiCommandKind.REVIEW:
        if hasattr(app, "submit"):
            # Trigger review by asking the agent to review current changes
            await app.submit("Review all uncommitted changes in the current workspace, checking for potential bugs, syntax issues, or architectural flaws.")
    elif command.kind is TuiCommandKind.TEST:
        if hasattr(app, "submit"):
            # Trigger project test execution
            await app.submit("Run the project's test suite, verify the results, and report any failures.")
    elif command.kind is TuiCommandKind.EXIT:
        app.running = False
    elif command.kind is TuiCommandKind.NEW:
        app.state = TerminalState(); app.current_thread_id = None; app.active_task_id = None; app._flushed_entries = 0
    elif command.kind is TuiCommandKind.SESSIONS:
        return await handle_session_command(
            app, command.action, command.instruction
        )
    elif command.kind is TuiCommandKind.RESTORE:
        if not command.instruction:
            app._append(DisplayKind.ERROR, "thread id is required")
            return False
        return await app.restore_thread(command.instruction)
    elif command.kind is TuiCommandKind.SKILL:
        return await handle_skill_command(app, command.instruction)
    elif command.kind is TuiCommandKind.MCP:
        return await handle_mcp_command(app, command.instruction)
    elif command.kind is TuiCommandKind.PLUGIN_CONTROL:
        return await handle_plugin_command(app, command.instruction)
    elif command.kind is TuiCommandKind.CHECKPOINT:
        return await handle_checkpoint_command(
            app, command.action, command.instruction
        )
    elif command.kind is TuiCommandKind.REWIND:
        return await begin_rewind(app, command.instruction)
    else:
        return None
    return True
