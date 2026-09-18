"""Bounded local command execution for POSIX hosts."""
from __future__ import annotations

import asyncio
import inspect
import os
import signal
import subprocess
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Optional

from code_agent.core.cancellation import CancellationToken
from code_agent.policy.environment import sanitize_environment
from code_agent.workspace.paths import WorkspacePathGuard

from .errors import RuntimeStartError, RuntimeUnavailable
from .models import (
    CommandResult,
    CommandSpec,
    OutputChunk,
    RuntimeKind,
    ShellDialect,
    StreamName,
    TerminationReason,
)


OutputCallback = Callable[[OutputChunk], object]
_READ_SIZE = 65_536


class PosixLocalRuntime:
    """Execute bounded commands in a sanitized POSIX process group."""

    kind = RuntimeKind.LOCAL
    shell_dialect = ShellDialect.POSIX_SH
    shell_summary = "posix_sh (/bin/sh)"

    def __init__(
        self, root: os.PathLike[str] | str, allowed_env_names: Iterable[str] = ()
    ) -> None:
        if isinstance(allowed_env_names, (str, bytes)):
            raise TypeError("allowed_env_names must be an iterable of names")
        self._guard = WorkspacePathGuard(root)
        self._allowed_env_names = tuple(allowed_env_names)
        sanitize_environment({}, self._allowed_env_names)

    @property
    def root(self) -> Path:
        return self._guard.root

    def is_available(self) -> bool:
        return os.name == "posix"

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
        if os.name != "posix":
            raise RuntimeUnavailable("POSIX local runtime is unavailable")

        cwd = self._guard.resolve(spec.cwd)
        result_cwd = self._guard.relative(cwd).as_posix()
        argv, display = self._command(spec)
        environment = sanitize_environment(
            os.environ, self._allowed_env_names, spec.explicit_env
        )
        return await self._execute(
            argv, display, cwd, result_cwd, environment, spec, cancellation, on_output
        )

    def _command(self, spec: CommandSpec) -> tuple[tuple[str, ...], str]:
        if spec.argv is not None:
            return spec.argv, " ".join(spec.argv)
        script = spec.shell_script
        if script is None:
            raise RuntimeUnavailable("POSIX local runtime requires a posix_sh script")
        if script.dialect is not ShellDialect.POSIX_SH:
            raise RuntimeUnavailable("POSIX local runtime requires the posix_sh dialect")
        return ("/bin/sh", "-lc", script.text), "/bin/sh -lc <approved script>"

    async def _execute(
        self,
        argv: tuple[str, ...],
        display: str,
        cwd: Path,
        result_cwd: str,
        environment: Mapping[str, str],
        spec: CommandSpec,
        cancellation: CancellationToken,
        on_output: Optional[OutputCallback],
    ) -> CommandResult:
        loop = asyncio.get_running_loop()
        started = loop.time()
        if cancellation.is_cancelled:
            return _result(
                argv, display, None, TerminationReason.CANCELLED, b"", b"",
                loop.time() - started, False, frozenset(), result_cwd, cancellation.reason,
            )
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                env=dict(environment),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise RuntimeStartError(f"failed to start command: {display}") from error

        assert process.stdout is not None and process.stderr is not None
        stdout, stderr = bytearray(), bytearray()
        limited = asyncio.Event()
        truncated: set[StreamName] = set()

        async def emit(chunk: OutputChunk) -> None:
            if on_output is None:
                return
            outcome = on_output(chunk)
            if inspect.isawaitable(outcome):
                await outcome

        async def read_pipe(reader: asyncio.StreamReader, sink: bytearray, stream: StreamName) -> None:
            while True:
                data = await reader.read(_READ_SIZE)
                if not data:
                    return
                room = spec.max_output_bytes - len(stdout) - len(stderr)
                kept = data[:max(0, room)]
                if kept:
                    sink.extend(kept)
                    await emit(OutputChunk(stream, kept))
                if len(kept) != len(data):
                    truncated.add(stream)
                    limited.set()
                    return

        wait = asyncio.create_task(process.wait())
        cancelled = asyncio.create_task(cancellation.wait_async())
        timeout = asyncio.create_task(asyncio.sleep(spec.timeout_s))
        limit = asyncio.create_task(limited.wait())
        readers = (
            asyncio.create_task(read_pipe(process.stdout, stdout, StreamName.STDOUT)),
            asyncio.create_task(read_pipe(process.stderr, stderr, StreamName.STDERR)),
        )
        monitors = (wait, cancelled, timeout, limit)
        try:
            await asyncio.wait(monitors, return_when=asyncio.FIRST_COMPLETED)
            reason = (
                TerminationReason.OUTPUT_LIMIT if limited.is_set()
                else TerminationReason.CANCELLED if cancellation.is_cancelled
                else TerminationReason.EXITED if wait.done()
                else TerminationReason.TIMEOUT
            )
            if reason is not TerminationReason.EXITED:
                await _terminate_process_group(process)
            await wait
            await asyncio.gather(*readers, return_exceptions=True)
            return _result(
                argv, display,
                process.returncode if reason is TerminationReason.EXITED else None,
                reason, bytes(stdout), bytes(stderr), loop.time() - started,
                reason is TerminationReason.OUTPUT_LIMIT, frozenset(truncated), result_cwd,
                cancellation.reason if reason is TerminationReason.CANCELLED else None,
            )
        finally:
            for task in (*monitors, *readers):
                if not task.done():
                    task.cancel()
            await asyncio.gather(*monitors, *readers, return_exceptions=True)


async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=1.0)
        return
    except asyncio.TimeoutError:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    await process.wait()


def _result(
    argv: tuple[str, ...], display: str, returncode: int | None,
    reason: TerminationReason, stdout: bytes, stderr: bytes, duration: float,
    truncated: bool, streams: frozenset[StreamName], cwd: str,
    cancellation_reason: str | None,
) -> CommandResult:
    return CommandResult(
        argv, display, returncode, reason, stdout, stderr, duration, truncated,
        cwd, cancellation_reason, streams,
    )
