from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version as package_version

from .app import create_application
from .acp_adapter import serve_acp
from code_agent.config.loader import LocalConfigError, default_config_path, resolve_config_path
from code_agent.interfaces.attachment_input import DEFAULT_ATTACHMENT_PROMPT
from code_agent.interfaces.commands import CommandKind, execute_command, parse_command
from code_agent.runtime.errors import RuntimeUnavailable
from .stdio import configure_windows_utf8_stdio


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
  -h, --help                   Show this help
  -V, --version                Show the installed version

Run without a command to open the Windows Terminal UI.
"""

_ACP_HELP = """Usage: chaos-agent [global options] acp

Serve Agent Client Protocol v1 over stdio for an editor client.
The process working directory is the single ACP workspace root.
"""


async def run(arguments: Sequence[str]) -> int:
    try:
        attachment_paths, without_attachments = _split_attachment_options(arguments)
        mode_name, remaining = _split_mode_option(without_attachments)
        profile_name, model_name, command_arguments = _split_global_options(remaining)
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
        print(f"usage error: {error}", file=sys.stderr)
        return 2
    application = None
    try:
        application = create_application(
            model_name=model_name, profile_name=profile_name, mode_name=mode_name
        )
        await application.startup()
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
        print(f"agent error: {type(error).__name__}", file=sys.stderr)
        return 1
    finally:
        if application is not None:
            await application.aclose()


def main() -> int:
    configure_windows_utf8_stdio()
    try:
        return asyncio.run(run(sys.argv[1:]))
    except KeyboardInterrupt:
        return 130


def _meta_command_output(arguments: Sequence[str]) -> str | None:
    values = tuple(arguments)
    if values in {("-h",), ("--help",)}:
        return _HELP.rstrip()
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


def _split_global_options(arguments: Sequence[str]) -> tuple[str | None, str | None, tuple[str, ...]]:
    """Extract order-independent global profile options before command grammar."""
    values = tuple(arguments)
    if not all(isinstance(value, str) for value in values):
        raise TypeError("command arguments must be text")
    result: dict[str, str] = {}; command: list[str] = []; index = 0
    while index < len(values):
        item = values[index]
        if item not in {"--model", "--profile"}:
            command.append(item); index += 1; continue
        if item in result or index + 1 >= len(values) or not values[index + 1].strip():
            raise ValueError(f"{item} requires one non-blank value and may be specified once")
        result[item] = values[index + 1]; index += 2
    return result.get("--profile"), result.get("--model"), tuple(command)


def _split_mode_option(arguments: Sequence[str]) -> tuple[str | None, tuple[str, ...]]:
    values = tuple(arguments)
    result: list[str] = []
    selected: str | None = None
    index = 0
    while index < len(values):
        if values[index] != "--mode":
            result.append(values[index])
            index += 1
            continue
        if selected is not None or index + 1 >= len(values):
            raise ValueError("--mode requires one value and may be specified once")
        selected = values[index + 1]
        if selected not in {"low", "medium", "high", "ultra"}:
            raise ValueError("--mode must be low, medium, high, or ultra")
        index += 2
    return selected, tuple(result)


def _split_attachment_options(
    arguments: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    values = tuple(arguments)
    if not all(isinstance(value, str) for value in values):
        raise TypeError("command arguments must be text")
    paths: list[str] = []
    remaining: list[str] = []
    index = 0
    while index < len(values):
        if values[index] != "--attach":
            remaining.append(values[index])
            index += 1
            continue
        if index + 1 >= len(values) or not values[index + 1].strip():
            raise ValueError("--attach requires one non-blank path")
        paths.append(values[index + 1])
        index += 2
    return tuple(paths), tuple(remaining)


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
