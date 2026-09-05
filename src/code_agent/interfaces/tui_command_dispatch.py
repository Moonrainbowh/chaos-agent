from __future__ import annotations

from .command_availability import available_services
from .evidence_view import format_evidence_summary
from .i18n import localize_task_status
from .terminal_display import DisplayKind
from .status_report import format_status
from .tui_attachment_commands import handle_attachment_command
from .tui_builtin_commands import handle_builtin_command
from .tui_commands import ParseOutcome, TuiCommandKind
from .tui_lifecycle import format_command_help
from .tui_permission_commands import handle_permission_command
from .tui_runtime_commands import set_effort, set_model, set_task_mode, show_task_modes
from .runtime_picker import selection_blocked_reason
from .tui_semantic_insight_commands import handle_semantic_insight_command
from .tui_workflow_commands import handle_workflow_command
from .tui_theme_commands import set_theme


async def handle_tui_command(app: object, outcome: ParseOutcome) -> bool:
    command = outcome.command
    assert command is not None
    if (builtin := await handle_builtin_command(app, command)) is not None:
        return builtin
    if command.kind is TuiCommandKind.THEME:
        return set_theme(app, command.instruction)
    if command.kind is TuiCommandKind.DIFF:
        await app.interactions.show_diff(app)
    elif command.kind is TuiCommandKind.ATTACHMENT:
        return await handle_attachment_command(app, command.instruction)
    elif command.kind is TuiCommandKind.STATUS:
        _show_status(app)
    elif command.kind is TuiCommandKind.MAP:
        return await handle_semantic_insight_command(app, command.instruction, command.action)
    elif command.kind is TuiCommandKind.HELP:
        return _show_help(app, command.instruction)
    elif command.kind is TuiCommandKind.WORKFLOW:
        return await handle_workflow_command(app, command.instruction)
    elif command.kind is TuiCommandKind.PLUGIN:
        return await _run_plugin(app, command.command_name, command.instruction)
    elif command.kind is TuiCommandKind.MODEL:
        return await set_model(app, command.instruction)
    elif command.kind is TuiCommandKind.EFFORT:
        return await set_effort(app, command.instruction)
    elif command.kind is TuiCommandKind.MODE:
        return await _set_mode(app, command.instruction, command.action)
    elif command.kind is TuiCommandKind.PERMISSION:
        return await handle_permission_command(
            app, command.instruction, command.action
        )
    elif command.kind is TuiCommandKind.EVIDENCE:
        return await _show_evidence(app, command.instruction)
    elif app.tasks and command.kind is TuiCommandKind.TASKS:
        await _show_tasks(app)
    elif app.tasks and command.kind is TuiCommandKind.ACCEPT:
        return await _accept_partial(app, command)
    else:
        app._append(DisplayKind.ERROR, "command is unavailable")
    return True


async def _accept_partial(app: object, command: object) -> bool:
    task_id = command.task_id or app.active_task_id
    if not task_id:
        app._append(DisplayKind.ERROR, "no active task")
        return False
    await app.tasks.accept_partial(
        task_id, command.instruction or "user accepted partial delivery"
    )
    if app.active_task_id == task_id:
        app.active_task_id = None
        app.state.status = "accepted_partial"
        app.state.task_status = "accepted_partial"
        app.state.pending_decision = None
    app._append(DisplayKind.METADATA, "Partial delivery accepted; verification is not complete.")
    return True


def _show_status(app: object) -> None:
    app._append(DisplayKind.METADATA, format_status(app))


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
    task_mode = await set_task_mode(app, instruction, action)
    if task_mode is not None:
        return task_mode
    if instruction is None:
        return show_task_modes(app)
    runtime = getattr(app, "runtime_selection", None)
    if runtime is None:
        return await _set_legacy_mode(app, instruction)
    if action not in {"agent", "model", "effort", "代理", "模型", "思考"}:
        if getattr(app, "modes", None) is not None:
            return await _set_legacy_mode(app, instruction)
        app._append(
            DisplayKind.ERROR,
            "use /mode agent, /mode model, or /mode effort",
        )
        return False
    try:
        reason = selection_blocked_reason(app)
        if reason:
            raise RuntimeError(reason)
        values = _runtime_axis_values(runtime, action, _action_argument(instruction))
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


def _runtime_axis_values(runtime: object, action: str, argument: str) -> dict[str, str]:
    if action in {"agent", "代理"}:
        if argument not in {"single", "team"}:
            raise ValueError("topology must be single or team")
        return {"topology": argument}
    if action in {"model", "模型"}:
        return {"profile": _resolve_profile(argument, runtime.profiles())}
    efforts = getattr(runtime, "list_reasoning_efforts", lambda: ("low", "medium", "high", "xhigh", "max"))()
    if argument not in efforts:
        raise ValueError("reasoning effort must be " + ", ".join(efforts))
    return {"reasoning_effort": argument}


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
        reason = selection_blocked_reason(app)
        if reason:
            raise RuntimeError(reason)
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


async def _show_tasks(app: object) -> None:
    records = await app.tasks.list(include_terminal=False)
    value = "\n".join(
        f"{item.id} · {localize_task_status(item.status.value, app.catalog)} "
        f"· {item.contract.objective[:80]}"
        for item in records
    ) or "no active or queued tasks"
    app._append(DisplayKind.METADATA, value)
