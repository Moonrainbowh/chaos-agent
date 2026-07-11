from __future__ import annotations

import asyncio
import inspect
import os
import shutil
import subprocess
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import psutil

from code_agent.core.cancellation import CancellationToken
from code_agent.policy.environment import sanitize_environment
from code_agent.workspace.paths import WorkspacePathGuard

from ._powershell_script import temporary_powershell_script
from ._process_snapshot import (
    capture_process_identity,
    resume_process_identity,
)
from ._windows_directory import DirectoryLease
from ._windows_process import finish_tasks, terminate_process_tree
from ._windows_spawn import spawn_suspended_process
from .errors import (
    ProcessTreeTerminationError,
    RuntimeStartError,
    RuntimeUnavailable,
)
from .models import (
    CommandResult,
    CommandSpec,
    OutputChunk,
    RuntimeKind,
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

    def __init__(self, root: os.PathLike[str] | str, allowed_env_names: Iterable[str] = ()) -> None:
        if isinstance(allowed_env_names, (str, bytes)):
            raise TypeError("allowed_env_names must be an iterable of names")
        self._guard = WorkspacePathGuard(root)
        self._allowed_env_names = tuple(allowed_env_names)
        sanitize_environment({}, self._allowed_env_names)

    @property
    def root(self) -> Path:
        return self._guard.root

    def is_available(self) -> bool:
        return True

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
        with self._prepare_command(spec) as (argv, display_command):
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

    @staticmethod
    @contextmanager
    def _prepare_command(
        spec: CommandSpec,
    ) -> Iterator[tuple[tuple[str, ...], str]]:
        if spec.argv is not None:
            yield spec.argv, subprocess.list2cmdline(spec.argv)
            return
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if executable is None:
            raise RuntimeUnavailable("PowerShell executable was not found")
        assert spec.powershell_script is not None
        with temporary_powershell_script(spec.powershell_script) as script_path:
            argv = (
                executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(script_path),
            )
            yield argv, "<powershell-script>"

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
            process, root_identity = await spawn_suspended_process(
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
        stdout = bytearray()
        stderr = bytearray()
        limit_reached = asyncio.Event()
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

            if reason is not TerminationReason.EXITED or primary_error is not None:
                await terminate_process_tree(process, process_wait, root_identity)
            await finish_tasks(readers)
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
                result_cwd,
                cancellation.reason if reason is TerminationReason.CANCELLED else None,
            )
        except BaseException as error:
            if (
                process.returncode is None
                and not isinstance(error, ProcessTreeTerminationError)
            ):
                await asyncio.shield(
                    terminate_process_tree(
                        process, process_wait, root_identity
                    )
                )
            raise
        finally:
            pending = (*monitors, *readers)
            for task in pending:
                if not task.done():
                    task.cancel()
            await finish_tasks(pending)

    @staticmethod
    def _result(
        argv: tuple[str, ...],
        display_command: str,
        returncode: Optional[int],
        reason: TerminationReason,
        stdout: bytes,
        stderr: bytes,
        duration_s: float,
        truncated: bool,
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
        )
