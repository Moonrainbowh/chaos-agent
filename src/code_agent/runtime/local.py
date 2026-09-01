from __future__ import annotations

import asyncio
import inspect
import os
import subprocess
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Optional

import psutil

from code_agent.core.cancellation import CancellationToken
from code_agent.policy.environment import sanitize_environment
from code_agent.workspace.paths import WorkspacePathGuard

from ._powershell_runtime import PowerShellRuntimeResolver
from ._windows_command import prepared_windows_command
from ._process_snapshot import (
    capture_process_identity,
    resume_process_identity,
)
from ._windows_directory import DirectoryLease
from ._windows_process import (
    close_process_job,
    complete_process_termination,
    finish_process_tasks,
    require_output_tasks,
)
from ._windows_spawn import spawn_suspended_process
from .errors import RuntimeStartError, RuntimeUnavailable
from .models import (
    CommandResult,
    CommandSpec,
    OutputChunk,
    PowerShellRuntimeInfo,
    RuntimeKind,
    ShellDialect,
    StreamName,
    TerminationReason,
)


OutputCallback = Callable[[OutputChunk], object]

_READ_SIZE = 65_536
_CREATE_SUSPENDED = 0x00000004
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


class WindowsLocalRuntime:
    """Execute bounded commands in a sanitized Windows child environment."""

    kind = RuntimeKind.LOCAL

    def __init__(
        self,
        root: os.PathLike[str] | str,
        allowed_env_names: Iterable[str] = (),
        *,
        powershell: PowerShellRuntimeResolver | None = None,
    ) -> None:
        if isinstance(allowed_env_names, (str, bytes)):
            raise TypeError("allowed_env_names must be an iterable of names")
        if powershell is not None and not isinstance(
            powershell, PowerShellRuntimeResolver
        ):
            raise TypeError("powershell must be a PowerShellRuntimeResolver or None")
        self._guard = WorkspacePathGuard(root)
        self._allowed_env_names = tuple(allowed_env_names)
        self._powershell = powershell or PowerShellRuntimeResolver()
        sanitize_environment({}, self._allowed_env_names)

    @property
    def root(self) -> Path:
        return self._guard.root

    def is_available(self) -> bool:
        return True

    def powershell_info(self) -> PowerShellRuntimeInfo:
        return self._powershell.resolve()

    async def run(
        self,
        spec: CommandSpec,
        cancellation: CancellationToken,
        on_output: Optional[OutputCallback],
    ) -> CommandResult:
        if not isinstance(spec, CommandSpec):
            raise TypeError("spec must be a CommandSpec")
        if not isinstance(cancellation, CancellationToken):
            raise TypeError("cancellation must be a CancellationToken")
        if on_output is not None and not callable(on_output):
            raise TypeError("on_output must be callable or None")

        cwd = self._guard.resolve(spec.cwd)
        result_cwd = self._guard.relative(cwd).as_posix()
        powershell = None
        if spec.argv is None:
            if (
                spec.shell_script is not None
                and spec.shell_script.dialect not in {
                    ShellDialect.POWERSHELL_7,
                    ShellDialect.WINDOWS_POWERSHELL_5_1,
                }
            ):
                raise RuntimeUnavailable(
                    "local Windows runtime requires a PowerShell dialect"
                )
            powershell = self.powershell_info()
        with prepared_windows_command(spec, powershell) as (argv, display_command):
            environment = sanitize_environment(
                os.environ, self._allowed_env_names, spec.explicit_env
            )
            return await self._execute(
                argv=argv,
                display_command=display_command,
                cwd=cwd,
                result_cwd=result_cwd,
                environment=environment,
                timeout_s=spec.timeout_s,
                max_output_bytes=spec.max_output_bytes,
                cancellation=cancellation,
                on_output=on_output,
            )

    async def _execute(
        self,
        *,
        argv: tuple[str, ...],
        display_command: str,
        cwd: Path,
        result_cwd: str,
        environment: Mapping[str, str],
        timeout_s: float,
        max_output_bytes: int,
        cancellation: CancellationToken,
        on_output: Optional[OutputCallback],
    ) -> CommandResult:
        loop = asyncio.get_running_loop()
        started = loop.time()
        if cancellation.is_cancelled:
            return self._result(
                argv, display_command, None, TerminationReason.CANCELLED,
                b"", b"", loop.time() - started, False, result_cwd,
                cancellation.reason,
            )

        try:
            process, root_identity, job = await spawn_suspended_process(
                argv,
                cwd=cwd,
                environment=environment,
                creationflags=_CREATE_NEW_PROCESS_GROUP | _CREATE_SUSPENDED,
                guard=self._guard,
                lease_factory=DirectoryLease,
                create_process=asyncio.create_subprocess_exec,
                capture_identity=capture_process_identity,
                resume_identity=resume_process_identity,
                process_api=psutil,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise RuntimeStartError(
                f"failed to start command: {display_command}"
            ) from error

        assert process.stdout is not None and process.stderr is not None
        stdout, stderr = bytearray(), bytearray()
        limit_reached = asyncio.Event()
        truncated_streams: set[StreamName] = set()
        reader_error: asyncio.Future[BaseException] = loop.create_future()

        async def emit(chunk: OutputChunk) -> None:
            if on_output is None:
                return
            returned = on_output(chunk)
            if inspect.isawaitable(returned):
                await returned

        async def read_pipe(
            reader: asyncio.StreamReader,
            sink: bytearray,
            stream: StreamName,
        ) -> None:
            try:
                while True:
                    data = await reader.read(_READ_SIZE)
                    if not data:
                        return
                    remaining = max_output_bytes - len(stdout) - len(stderr)
                    kept = data[: max(0, remaining)]
                    if kept:
                        sink.extend(kept)
                        await emit(OutputChunk(stream, kept))
                    if len(kept) != len(data):
                        truncated_streams.add(stream)
                        limit_reached.set()
                        return
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                if not reader_error.done():
                    reader_error.set_result(error)

        process_wait = asyncio.create_task(process.wait())
        cancellation_wait = asyncio.create_task(cancellation.wait_async())
        timeout_wait = asyncio.create_task(
            asyncio.sleep(max(0.0, started + timeout_s - loop.time()))
        )
        limit_wait = asyncio.create_task(limit_reached.wait())
        readers = (
            asyncio.create_task(read_pipe(process.stdout, stdout, StreamName.STDOUT)),
            asyncio.create_task(read_pipe(process.stderr, stderr, StreamName.STDERR)),
        )
        monitors: tuple[asyncio.Future[object], ...] = (
            process_wait,
            cancellation_wait,
            timeout_wait,
            limit_wait,
            reader_error,
        )
        reason = TerminationReason.EXITED
        primary_error: Optional[BaseException] = None
        termination_started = False

        try:
            await asyncio.wait(monitors, return_when=asyncio.FIRST_COMPLETED)
            if reader_error.done():
                primary_error = reader_error.result()
            elif limit_reached.is_set():
                reason = TerminationReason.OUTPUT_LIMIT
            elif cancellation.is_cancelled:
                reason = TerminationReason.CANCELLED
            elif process_wait.done():
                reason = TerminationReason.EXITED
            else:
                reason = TerminationReason.TIMEOUT

            termination_started = True
            await complete_process_termination(process, process_wait, root_identity, job)
            close_process_job(job, process.pid)
            await require_output_tasks(readers, process.pid)
            if limit_reached.is_set():
                reason = TerminationReason.OUTPUT_LIMIT
            if reader_error.done() and primary_error is None:
                primary_error = reader_error.result()
            if primary_error is not None:
                raise primary_error

            returncode = process.returncode if reason is TerminationReason.EXITED else None
            return self._result(
                argv,
                display_command,
                returncode,
                reason,
                bytes(stdout),
                bytes(stderr),
                loop.time() - started,
                reason is TerminationReason.OUTPUT_LIMIT,
                frozenset(truncated_streams),
                result_cwd,
                cancellation.reason if reason is TerminationReason.CANCELLED else None,
            )
        except BaseException as error:
            if not job.closed and not termination_started:
                await complete_process_termination(
                    process, process_wait, root_identity, job
                )
            raise
        finally:
            pending = (*monitors, *readers)
            await finish_process_tasks(job, process.pid, pending)

    @staticmethod
    def _result(
        argv: tuple[str, ...],
        display_command: str,
        returncode: Optional[int],
        reason: TerminationReason,
        stdout: bytes,
        stderr: bytes,
        duration_s: float,
        truncated: bool, truncated_streams: frozenset[StreamName],
        cwd: str,
        cancellation_reason: Optional[str],
    ) -> CommandResult:
        return CommandResult(
            argv=argv,
            display_command=display_command,
            returncode=returncode,
            reason=reason,
            stdout=stdout,
            stderr=stderr,
            duration_s=duration_s,
            truncated=truncated,
            cwd=cwd,
            cancellation_reason=cancellation_reason,
            truncated_streams=truncated_streams,
        )
