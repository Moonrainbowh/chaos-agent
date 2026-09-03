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
from .tui_permission_commands import handle_permission_command
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
        return await _set_mode(app, command.instruction, command.action)
    elif command.kind is TuiCommandKind.PERMISSION:
        return await handle_permission_command(
            app, command.instruction, command.action
        )
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
    app._append(DisplayKind.METADATA, value + _host_runtime_suffix(app))


def _show_help(app: object, instruction: str | None) -> bool:
    available = app.command_registry.available(available_services(app))
    if instruction and instruction.casefold() in {"全部", "all"}:
        app._append(
            DisplayKind.METADATA,
            format_command_help(app.command_registry.all()),
        )
    elif instruction:
        spec = app.command_registry.resolve(instruction)
        if spec is None or spec not in available:
            app._append(DisplayKind.ERROR, "unknown or unavailable slash command")
            return False
        app._append(DisplayKind.METADATA, f"{spec.display} · {spec.description}")
    else:
        app._append(
            DisplayKind.METADATA,
            format_command_help(app.command_registry.primary()),
        )
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


async def _set_mode(
    app: object,
    instruction: str | None,
    action: str | None = None,
) -> bool:
    runtime = getattr(app, "runtime_selection", None)
    if runtime is None:
        return await _set_legacy_mode(app, instruction)
    if instruction is None:
        current = runtime.current
        profiles = " | ".join(
            f"{_profile_name(item)}:{_profile_model(item)}"
            for item in runtime.profiles()
        )
        app._append(
            DisplayKind.METADATA,
            "current " + _runtime_summary(current) + " | " + profiles
            + _host_runtime_suffix(app),
        )
        return True
    if action not in {"代理", "模型", "思考"}:
        if getattr(app, "modes", None) is not None:
            return await _set_legacy_mode(app, instruction)
        app._append(
            DisplayKind.ERROR,
            "use /模式 代理, /模式 模型, or /模式 思考",
        )
        return False
    argument = _action_argument(instruction)
    values: dict[str, object]
    try:
        if action == "代理":
            if argument not in {"single", "team"}:
                raise ValueError("topology must be single or team")
            values = {"topology": argument}
        elif action == "模型":
            values = {"profile": _resolve_profile(argument, runtime.profiles())}
        else:
            if argument not in {"low", "medium", "high", "xhigh", "max"}:
                raise ValueError(
                    "reasoning effort must be low, medium, high, xhigh, or max"
                )
            values = {"reasoning_effort": argument}
        selected = await runtime.use(
            **values,
            idle=app._run_task is None or app._run_task.done(),
        )
    except (RuntimeError, TypeError, ValueError) as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
    app._append(
        DisplayKind.METADATA,
        "runtime selected: " + _runtime_summary(selected) + _host_runtime_suffix(app),
    )
    return True


async def _set_legacy_mode(app: object, instruction: str | None) -> bool:
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
    runtime = getattr(app, "runtime_selection", None)
    detail = (
        _runtime_summary(runtime.current)
        if runtime is not None
        else selected.model
    )
    app._append(
        DisplayKind.METADATA,
        f"mode selected: {selected.name} · {detail}" + _host_runtime_suffix(app),
    )
    return True


def _action_argument(instruction: str) -> str:
    parts = instruction.split(maxsplit=1)
    return parts[1] if len(parts) == 2 else ""


def _resolve_profile(query: str, profiles: object) -> str:
    names = tuple(_profile_name(item) for item in profiles)
    folded = query.casefold()
    exact = tuple(name for name in names if name.casefold() == folded)
    if len(exact) == 1:
        return exact[0]
    suffix = tuple(
        name
        for name in names
        if any(name.casefold().endswith(separator + folded) for separator in ("_", "-", "."))
    )
    if len(suffix) == 1:
        return suffix[0]
    if not suffix:
        raise ValueError(f"unknown model profile: {query}")
    raise ValueError(f"ambiguous model profile suffix: {query}")


def _profile_name(profile: object) -> str:
    return str(profile[0] if isinstance(profile, (tuple, list)) else profile.name)


def _profile_model(profile: object) -> str:
    return str(profile[1] if isinstance(profile, (tuple, list)) else profile.model)


def _runtime_summary(selection: object) -> str:
    return " · ".join(
        str(getattr(getattr(selection, name), "value", getattr(selection, name)))
        for name in ("topology", "profile", "model", "reasoning_effort")
    )


def _host_runtime_suffix(app: object) -> str:
    summary = getattr(app, "host_runtime_summary", None)
    if not isinstance(summary, str) or not summary.strip():
        return ""
    return " · host: " + summary


async def _show_evidence(app: object, instruction: str | None) -> bool:
    task_id = instruction or app.active_task_id or app.state.task_id
    if app.evidence is None or not task_id:
        app._append(DisplayKind.ERROR, "evidence is unavailable")
        return False
    records = await app.evidence.list_verification_evidence(task_id)
    app._append(DisplayKind.METADATA, format_evidence_summary(records))
    return True
