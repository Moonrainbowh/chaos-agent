from __future__ import annotations

import shutil
from typing import Any

from code_agent.core.cancellation import CancellationToken

from .cost_control import format_cost_report
from .diagnostic_view import format_diagnostics
from .terminal_display import DisplayKind
from .terminal_state import TerminalState
from .terminal_tail import clear_live_tail
from .tui_commands import TuiCommand, TuiCommandKind


async def handle_general_command(app: Any, command: TuiCommand) -> bool | None:
    if command.kind is TuiCommandKind.CLEAR:
        return _clear(app)
    if command.kind is TuiCommandKind.COMPACT:
        return await _compact(app)
    if command.kind is TuiCommandKind.COST:
        return await _cost(app)
    if command.kind is TuiCommandKind.DOCTOR:
        return await _doctor(app)
    if command.kind is TuiCommandKind.REVIEW:
        return await _review(app, command.instruction)
    if command.kind is TuiCommandKind.TEST:
        return await _test(app, command.instruction)
    if command.kind is TuiCommandKind.EXIT:
        app.running = False
        return True
    return None


def _idle(app: Any) -> bool:
    return app._run_task is None or app._run_task.done()


def _clear(app: Any) -> bool:
    if not _idle(app):
        app._append(DisplayKind.ERROR, "clear is available only when idle")
        return False
    height = shutil.get_terminal_size((100, 30)).lines
    app._write(clear_live_tail(app._tail_geometry, terminal_height=height))
    app._write("\x1b[2J\x1b[H")
    app.state.entries.clear()
    app.state.transcript.clear()
    app.state.timeline.clear()
    app.state._draft.clear()
    app.state.execution_summary = ""
    app._flushed_entries = 0
    app._tail_geometry = None
    return True


async def _compact(app: Any) -> bool:
    if not _idle(app):
        app._append(DisplayKind.ERROR, "compact is available only when idle")
        return False
    if not app.current_thread_id:
        app._append(DisplayKind.ERROR, "no current thread to compact")
        return False
    app._append(DisplayKind.METADATA, "Compacting durable context...")
    try:
        report = await app.controller.compact_context(
            app.current_thread_id, CancellationToken()
        )
    except (RuntimeError, TypeError, ValueError) as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
    checkpoint = getattr(report, "checkpoint_id", None)
    detail = (
        f"messages {report.before_messages} -> {report.after_messages}; "
        f"estimated tokens {report.before_tokens:,} -> {report.after_tokens:,}"
    )
    if not checkpoint:
        reason = (
            "semantic summarization fell back"
            if getattr(report, "fallback_used", False)
            else "the context is too short"
        )
        app._append(
            DisplayKind.WARNING,
            f"No reusable checkpoint was created ({reason}); {detail}",
        )
        return False
    detail += f"; checkpoint {checkpoint} persisted"
    app._append(DisplayKind.SUCCESS, "Context compacted: " + detail)
    return True


async def _cost(app: Any) -> bool:
    if app.costs is None:
        app._append(DisplayKind.ERROR, "durable cost accounting is unavailable")
        return False
    try:
        report = await app.costs.report(
            task_id=app.active_task_id or app.state.task_id,
            thread_id=app.current_thread_id,
        )
    except (KeyError, RuntimeError, ValueError) as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
    app._append(DisplayKind.METADATA, format_cost_report(report))
    return True


async def _doctor(app: Any) -> bool:
    if app.doctor is None:
        app._append(DisplayKind.ERROR, "system diagnostics are unavailable")
        return False
    try:
        checks = await app.doctor.run()
    except (OSError, RuntimeError, ValueError) as error:
        app._append(DisplayKind.ERROR, f"doctor failed: {error}")
        return False
    app._append(DisplayKind.METADATA, format_diagnostics(checks))
    return not any(item.status == "fail" for item in checks)


async def _review(app: Any, scope: str | None) -> bool:
    target = scope or "all uncommitted changes"
    return await app.submit(
        "Review " + target + " in the current workspace. Inspect the actual diff, "
        "prioritize correctness and regressions, cite precise files and lines, and "
        "report findings before any summary. Do not modify files."
    )


async def _test(app: Any, scope: str | None) -> bool:
    target = scope or "the current project"
    return await app.submit(
        "Verify " + target + ". Discover the repository's supported test commands, "
        "run the relevant checks, and report exact commands, pass/fail counts, and "
        "unverified gaps. Do not claim success from an unexecuted test."
    )
