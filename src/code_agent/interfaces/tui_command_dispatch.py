from __future__ import annotations

from .command_availability import available_services
from .evidence_view import format_evidence_summary
from .i18n import localize_task_status
from .terminal_display import DisplayKind
from .terminal_status import status_snapshot
from .tui_attachment_commands import handle_attachment_command
from .tui_builtin_commands import handle_builtin_command
from .tui_commands import ParseOutcome, TuiCommandKind
from .tui_lifecycle import format_command_help
from .tui_workflow_commands import handle_workflow_command


async def handle_tui_command(app: object, outcome: ParseOutcome) -> bool:
    command = outcome.command
    assert command is not None
    builtin = await handle_builtin_command(app, command)
    if builtin is not None:
        return builtin
    if command.kind is TuiCommandKind.DIFF:
        await app.interactions.show_diff(app)
    elif command.kind is TuiCommandKind.ATTACHMENT:
        return await handle_attachment_command(app, command.instruction)
    elif command.kind is TuiCommandKind.STATUS:
        _show_status(app)
    elif command.kind is TuiCommandKind.HELP:
        return _show_help(app, command.instruction)
    elif command.kind is TuiCommandKind.WORKFLOW:
        return await handle_workflow_command(app, command.instruction)
    elif command.kind is TuiCommandKind.PLUGIN:
        return await _run_plugin(app, command.command_name, command.instruction)
    elif command.kind is TuiCommandKind.MODE:
        return await _set_mode(app, command.instruction)
    elif command.kind is TuiCommandKind.PERMISSION:
        return await _set_permission(app, command.instruction)
    elif command.kind is TuiCommandKind.EVIDENCE:
        return await _show_evidence(app, command.instruction)
    elif app.tasks and command.kind is TuiCommandKind.TASKS:
        records = await app.tasks.list(include_terminal=True)
        value = " | ".join(
            f"{item.id}:{localize_task_status(item.status.value, app.catalog)}"
            for item in records
        )
        app._append(DisplayKind.METADATA, value)
    elif app.tasks and command.kind is TuiCommandKind.ACCEPT:
        task_id = command.task_id or app.active_task_id
        if not task_id:
            app._append(DisplayKind.ERROR, "no active task")
            return False
        await app.tasks.accept_partial(
            task_id, command.instruction or "user accepted partial delivery"
        )
    else:
        app._append(DisplayKind.ERROR, "command is unavailable")
    return True


def _show_status(app: object) -> None:
    task_id = app.active_task_id or app.state.task_id
    value = status_snapshot(
        app.state.status, task_id, app.current_thread_id, app._current_model()
    )
    app._append(DisplayKind.METADATA, value)


def _show_help(app: object, instruction: str | None) -> bool:
    available = app.command_registry.available(available_services(app))
    if instruction:
        spec = app.command_registry.resolve(instruction)
        if spec is None or spec not in available:
            app._append(DisplayKind.ERROR, "unknown or unavailable slash command")
            return False
        app._append(DisplayKind.METADATA, f"{spec.display} · {spec.description}")
    else:
        app._append(DisplayKind.METADATA, format_command_help(available))
    return True


async def _run_plugin(
    app: object, command_name: str | None, instruction: str | None
) -> bool:
    if app.plugins is None or command_name is None:
        app._append(DisplayKind.ERROR, "plugin command is unavailable")
        return False
    try:
        await app.plugins.execute_command(
            command_name, tuple((instruction or "").split())
        )
    except (KeyError, PermissionError, RuntimeError, ValueError):
        app._append(DisplayKind.ERROR, "plugin command failed")
        return False
    return True


async def _set_mode(app: object, instruction: str | None) -> bool:
    if app.modes is None:
        app._append(DisplayKind.ERROR, "agent modes are unavailable")
        return False
    if instruction is None:
        current = app.modes.current
        choices = " | ".join(
            f"{item.name}:{item.model}" for item in app.modes.list()
        )
        app._append(
            DisplayKind.METADATA,
            f"current {current.name}:{current.model} | {choices}",
        )
        return True
    try:
        selected = await app.modes.use(
            instruction, idle=app._run_task is None or app._run_task.done()
        )
    except (ValueError, RuntimeError) as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
    app._append(DisplayKind.METADATA, f"mode selected: {selected.name} · {selected.model}")
    return True


async def _set_permission(app: object, instruction: str | None) -> bool:
    if app.permissions is None:
        app._append(DisplayKind.ERROR, "permission controls are unavailable")
        return False
    if instruction is None:
        current = app.permissions.current
        choices = " | ".join(item.name for item in app.permissions.list())
        app._append(DisplayKind.METADATA, f"current {current.name} | {choices}")
        return True
    try:
        selected = await app.permissions.use(
            instruction, idle=app._run_task is None or app._run_task.done()
        )
    except (ValueError, RuntimeError) as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
    app._append(
        DisplayKind.METADATA,
        f"permission selected: {selected.name} · {selected.description}",
    )
    return True


async def _show_evidence(app: object, instruction: str | None) -> bool:
    task_id = instruction or app.active_task_id or app.state.task_id
    if app.evidence is None or not task_id:
        app._append(DisplayKind.ERROR, "evidence is unavailable")
        return False
    records = await app.evidence.list_verification_evidence(task_id)
    app._append(DisplayKind.METADATA, format_evidence_summary(records))
    return True
