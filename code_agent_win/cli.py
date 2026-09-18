from __future__ import annotations

import asyncio
import sys
import os
import traceback
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version as package_version

from code_agent.config.loader import LocalConfigError, default_config_path, resolve_config_path
from code_agent.interfaces.attachment_input import DEFAULT_ATTACHMENT_PROMPT
from code_agent.interfaces.commands import CommandKind, execute_command, parse_command
from code_agent.runtime.errors import RuntimeUnavailable
from .cli_options import (
    _split_attachment_options,
    _split_global_options,
    _split_isolation_option,
    _split_mode_option,
    _split_reclaim_option,
)
from .stdio import configure_windows_utf8_stdio
from .workspace_policy import (
    request_task_isolation,
    reset_task_isolation,
)


_ATTACHMENT_COMMANDS = frozenset(
    {
        CommandKind.TUI,
        CommandKind.ASK,
        CommandKind.RESUME,
        CommandKind.RUN_JSON,
        CommandKind.TASK_RESUME,
    }
)

_HELP = """Usage: chaos-agent [global options] [command]

Commands:
  auth <command>               Login, API keys, model catalog and configuration
  acp                          Serve ACP v1 over stdio for editor clients
  ask <prompt>                 Run one request and print the result
  resume <thread-id> [prompt]  Resume a saved task or open it in the TUI
  run --json <prompt>          Stream machine-readable JSON events
  task list                    List durable tasks
  task resume <task-id> [text] Resume a durable task

Global options:
  --profile <name>             Select a configured provider profile
  --model <name>               Override the selected model
  --mode <low|medium|high|ultra>
  --attach <path>              Attach a supported local file
  --isolated                   Run this invocation in an isolated Git worktree
  --reclaim-workspaces         Retire managed worktrees that hold no work, then exit
  -h, --help                   Show this help
  -V, --version                Show the installed version

Run without a command to open the interactive terminal UI.
"""

_ACP_HELP = """Usage: chaos-agent [global options] acp

Serve Agent Client Protocol v1 over stdio for an editor client.
The process working directory is the single ACP workspace root.
"""

_COMMAND_HELP = {
    "ask": "Usage: chaos-agent [global options] ask <prompt>\n\nRun one durable task and render its result.",
    "resume": "Usage: chaos-agent [global options] resume <thread-id> [prompt]\n\nResume a saved task or open its history in the TUI.",
    "run": "Usage: chaos-agent [global options] run --json <prompt>\n\nRun one durable task and stream JSON lifecycle events.",
    "task": "Usage: chaos-agent [global options] task list|resume <task-id> [prompt]\n\nInspect or resume durable tasks.",
}


def create_application(**kwargs):
    from .app import create_application as create
    return create(**kwargs)


async def serve_acp(application):
    from .acp_adapter import serve_acp as serve
    return await serve(application)


async def run(arguments: Sequence[str], *, splash=None) -> int:
    if arguments and arguments[0] == "auth":
        if splash is not None:
            splash.stop()
        from .auth_cli import run_auth
        return await run_auth(arguments[1:])
    isolated = reclaim = False
    try:
        attachment_paths, without_attachments = _split_attachment_options(arguments)
        mode_name, without_mode = _split_mode_option(without_attachments)
        isolated, without_isolation = _split_isolation_option(without_mode)
        reclaim, remaining = _split_reclaim_option(without_isolation)
        profile_name, model_name, command_arguments = _split_global_options(remaining)
        if reclaim and command_arguments:
            raise ValueError("--reclaim-workspaces must be used on its own")
        meta_output = _meta_command_output(command_arguments)
        if meta_output is not None:
            if attachment_paths:
                raise ValueError("--attach is not supported by help or version")
            print(meta_output)
            return 0
        is_acp = command_arguments == ("acp",)
        if is_acp and attachment_paths:
            raise ValueError("--attach is not supported by acp")
        if (
            not is_acp
            and command_arguments
            and command_arguments[0] == "acp"
        ):
            raise ValueError("acp does not accept positional arguments")
        if is_acp:
            command = None
        else:
            command_arguments = _default_attachment_prompt(
                command_arguments, bool(attachment_paths)
            )
            command = parse_command(command_arguments)
            _require_attachment_consumer(command.kind, attachment_paths)
    except (TypeError, ValueError) as error:
        if splash is not None:
            splash.stop()
        print(f"usage error: {error}", file=sys.stderr)
        return 2
    isolation_token = request_task_isolation("explicit") if isolated else None
    application = None
    try:
        try:
            application = create_application(
                model_name=model_name,
                profile_name=profile_name,
                mode_name=mode_name,
                restore_model_selection=(
                    command is not None
                    and command.kind is CommandKind.TUI
                    and profile_name is None
                    and model_name is None
                ),
            )
            await application.startup()
        finally:
            if splash is not None:
                splash.stop()
        if reclaim:
            return await _report_reclamation(application)
        if command is None:
            await serve_acp(application)
            return 0
        application.dispatcher.interactive = command.kind is CommandKind.TUI
        attachments = ()
        if attachment_paths:
            if command.kind is CommandKind.TUI:
                await application.tui.attachment_draft.add_paths(attachment_paths)
            else:
                attachments = await asyncio.to_thread(
                    application.attachment_ingestor.ingest_paths,
                    attachment_paths,
                    explicit_external=True,
                )
                application.tui.attachment_draft.validate(attachments)
        return await execute_command(
            command,
            application.controller,
            application.tui,
            sys.stdout.write,
            application.foreground_tasks,
            attachments=attachments if command.kind is not CommandKind.TUI else (),
        )
    except LocalConfigError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        print(
            f"Create {_configuration_path()} or configure CHAOS_* provider environment variables; see README.md.",
            file=sys.stderr,
        )
        return 2
    except RuntimeUnavailable as error:
        print(f"runtime unavailable: {error}", file=sys.stderr)
        print(
            "Install the configured PowerShell dialect or set "
            "[agent].powershell_dialect to an available runtime.",
            file=sys.stderr,
        )
        return 2
    except Exception as error:
        # Exception text may contain provider responses, paths, or user data.
        # The CLI contract exposes only the safe error category; diagnostics
        # remain available through the opt-in debug traceback below.
        print(f"agent error: {type(error).__name__}", file=sys.stderr)
        if os.environ.get("CHAOS_DEBUG_ERRORS") == "1":
            traceback.print_exc(file=sys.stderr)
        return 1
    finally:
        if isolation_token is not None:
            reset_task_isolation(isolation_token)
        if application is not None:
            await application.aclose()


def main(*, splash=None) -> int:
    configure_windows_utf8_stdio()
    try:
        if splash is None:
            from .bootstrap import _interactive
            from code_agent.interfaces.startup_splash import StartupSplash
            splash = StartupSplash()
            if _interactive(sys.argv[1:]) and sys.stdin.isatty():
                splash.start()
        return asyncio.run(run(sys.argv[1:], splash=splash))
    except KeyboardInterrupt:
        return 130
    finally:
        if splash is not None:
            splash.stop()


async def _report_reclamation(application) -> int:
    """Print what the explicit worktree reclamation kept and retired."""
    report = await application.workspace_runtime.reclaim_workspaces(
        rewindable=True
    )
    for item in report.reclaimed:
        print(f"reclaimed {item.root}: {item.reason}")
    for item in report.retained:
        print(f"kept {item.root}: {item.reason}")
    return 0


def _meta_command_output(arguments: Sequence[str]) -> str | None:
    values = tuple(arguments)
    if values in {("-h",), ("--help",)}:
        return _HELP.rstrip()
    if len(values) == 2 and values[0] in _COMMAND_HELP and values[1] in {
        "-h", "--help",
    }:
        return _COMMAND_HELP[values[0]]
    if values in {("acp", "-h"), ("acp", "--help")}:
        return _ACP_HELP.rstrip()
    if values in {
        ("-V",),
        ("--version",),
        ("acp", "-V"),
        ("acp", "--version"),
    }:
        try:
            installed = package_version("chaos-agent")
        except PackageNotFoundError:
            installed = "unknown"
        return f"chaos-agent {installed}"
    return None


def _configuration_path() -> str:
    try:
        return str(resolve_config_path())
    except LocalConfigError:
        return str(default_config_path())


def _default_attachment_prompt(
    arguments: Sequence[str], has_attachments: bool
) -> tuple[str, ...]:
    values = tuple(arguments)
    if not has_attachments:
        return values
    if values == ("ask",):
        return (*values, DEFAULT_ATTACHMENT_PROMPT)
    if values == ("run", "--json"):
        return (*values, DEFAULT_ATTACHMENT_PROMPT)
    if len(values) == 2 and values[0] == "resume":
        return (*values, DEFAULT_ATTACHMENT_PROMPT)
    return values


def _require_attachment_consumer(
    kind: CommandKind, paths: Sequence[str]
) -> None:
    if paths and kind not in _ATTACHMENT_COMMANDS:
        raise ValueError(f"--attach is not supported by {kind.value}")


if __name__ == "__main__":
    raise SystemExit(main())
