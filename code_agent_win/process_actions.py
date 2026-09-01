from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest, ActionResult
from code_agent.runtime.errors import RuntimeErrorBase
from code_agent.runtime.local import WindowsLocalRuntime
from code_agent.runtime.models import (
    CommandSpec,
    PowerShellRuntimeInfo,
    ShellScript,
)
from code_agent.runtime.output_codec import OutputEncoding

from code_agent_win.action_support import text_argument
from code_agent_win.tool_support import command_action_result


CacheInvalidator = Callable[[Sequence[str]], None]


async def run_powershell_action(
    request: ActionRequest,
    runtime: WindowsLocalRuntime,
    cancellation: CancellationToken,
    invalidate_cache: CacheInvalidator | None,
) -> ActionResult:
    command = text_argument(request.arguments, "command")
    supplier = getattr(runtime, "powershell_info", None)
    candidate = supplier() if callable(supplier) else None
    info = candidate if isinstance(candidate, PowerShellRuntimeInfo) else None
    spec = (
        CommandSpec(
            cwd=Path("."),
            shell_script=ShellScript(command, info.dialect),
        )
        if info is not None
        else CommandSpec(cwd=Path("."), powershell_script=command)
    )
    try:
        result = await runtime.run(
            spec,
            cancellation,
            None,
        )
    except RuntimeErrorBase as error:
        return _runtime_start_error(request, error)
    finally:
        if invalidate_cache is not None:
            invalidate_cache(())
    return command_action_result(
        request,
        result,
        powershell=info,
        stdout_encoding=OutputEncoding.UTF_8,
        stderr_encoding=OutputEncoding.UTF_8,
    )


async def run_process_action(
    request: ActionRequest,
    runtime: WindowsLocalRuntime,
    cancellation: CancellationToken,
    invalidate_cache: CacheInvalidator | None,
) -> ActionResult:
    arguments = request.arguments
    program = text_argument(arguments, "program")
    process_args = _process_args(arguments)
    cwd = arguments.get("cwd", ".")
    timeout_s = arguments.get("timeout_s", 60)
    stdout_encoding = _output_encoding(arguments, "stdout_encoding")
    stderr_encoding = _output_encoding(arguments, "stderr_encoding")
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("cwd must be non-empty text")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int):
        raise ValueError("timeout_s must be an integer")
    try:
        result = await runtime.run(
            CommandSpec(
                cwd=Path(cwd),
                argv=(program, *process_args),
                timeout_s=timeout_s,
            ),
            cancellation,
            None,
        )
    except RuntimeErrorBase as error:
        return _runtime_start_error(request, error)
    finally:
        if invalidate_cache is not None:
            invalidate_cache(())
    return command_action_result(
        request,
        result,
        stdout_encoding=stdout_encoding,
        stderr_encoding=stderr_encoding,
    )


def _runtime_start_error(
    request: ActionRequest, error: RuntimeErrorBase
) -> ActionResult:
    return ActionResult(
        request.id,
        request.name,
        {"error": "command failed", "detail": type(error).__name__},
        is_error=True,
        metadata={"execution_attempted": True},
    )


def _process_args(arguments: Mapping[str, object]) -> tuple[str, ...]:
    values = arguments.get("args")
    if not isinstance(values, Sequence) or isinstance(
        values, (str, bytes, bytearray)
    ):
        raise ValueError("args must be an array of strings")
    if not all(isinstance(item, str) for item in values):
        raise ValueError("args must contain only strings")
    return tuple(values)


def _output_encoding(
    arguments: Mapping[str, object], name: str
) -> OutputEncoding:
    value = arguments.get(name, OutputEncoding.UTF_8.value)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    try:
        return OutputEncoding(value)
    except ValueError:
        raise ValueError(f"{name} is not a supported output encoding") from None
