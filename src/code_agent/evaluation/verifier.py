from __future__ import annotations

import asyncio
import hashlib
import os
import signal
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from .models import VerifierOracle
from .paths import contained_path, ensure_no_link_parent


@dataclass(frozen=True)
class CommandOutcome:
    exit_code: int | None
    timed_out: bool = False
    infrastructure_failure: str | None = None


VerifierExecutor = Callable[[Path, VerifierOracle, str], Awaitable[CommandOutcome]]


class SubprocessVerifier:
    """Run trusted verifier commands without a shell in an isolated clone."""

    async def __call__(
        self,
        workspace: Path,
        oracle: VerifierOracle,
        _phase: str,
    ) -> CommandOutcome:
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
                "DOTNET_NOLOGO": "1",
            }
        )
        executable = shutil.which(oracle.argv[0])
        if executable is None:
            return CommandOutcome(None, infrastructure_failure=f"missing executable: {oracle.argv[0]}")
        preflight_failure = await _toolchain_preflight(executable)
        if preflight_failure is not None:
            return CommandOutcome(None, infrastructure_failure=preflight_failure)
        cwd = workspace if oracle.cwd == "." else contained_path(workspace, oracle.cwd, "verifier cwd")
        ensure_no_link_parent(workspace, cwd, "verifier cwd")
        if not cwd.is_dir():
            return CommandOutcome(None, infrastructure_failure=f"missing verifier cwd: {oracle.cwd}")
        process_options = process_group_options()
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                *oracle.argv[1:],
                cwd=str(cwd),
                env=environment,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                **process_options,
            )
        except OSError as error:
            return CommandOutcome(None, infrastructure_failure=f"verifier launch failed: {error}")
        try:
            code = await asyncio.wait_for(process.wait(), timeout=oracle.timeout_seconds)
            return CommandOutcome(code)
        except asyncio.TimeoutError:
            await terminate_process_tree(process)
            return CommandOutcome(None, True)


async def run_trusted_verifier(
    source: Path,
    verifier_root: Path,
    oracle: VerifierOracle,
    phase: str,
    execute: VerifierExecutor,
) -> CommandOutcome:
    """Clone a snapshot, inject hidden tests there, and execute one oracle."""
    destination = verifier_root / f"{phase}-{_safe_name(oracle.name)}"
    await asyncio.to_thread(shutil.copytree, source, destination)
    await asyncio.to_thread(_materialize_hidden, destination, oracle.hidden_files)
    return await execute(destination, oracle, phase)


def _materialize_hidden(workspace: Path, files: object) -> None:
    if not isinstance(files, dict):
        files = dict(files)
    for relative_path, content in files.items():
        path = contained_path(workspace, relative_path, "hidden verifier path")
        ensure_no_link_parent(workspace, path, "hidden verifier path")
        if path.exists():
            raise ValueError(f"hidden verifier file collides with fixture: {relative_path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _safe_name(value: str) -> str:
    readable = "".join(character if character.isalnum() else "-" for character in value)[:64]
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{readable}-{digest}"


async def _toolchain_preflight(executable: str) -> str | None:
    if Path(executable).stem.lower() != "dotnet":
        return None
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "--list-sdks",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            **process_group_options(),
        )
    except OSError as error:
        return f"dotnet SDK preflight failed: {error}"
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=10)
    except asyncio.TimeoutError:
        await terminate_process_tree(process)
        return "dotnet SDK preflight timed out"
    versions = output.decode("utf-8", errors="replace").splitlines()
    compatible = any(_dotnet_major(line) >= 8 for line in versions)
    return None if process.returncode == 0 and compatible else "missing compatible .NET SDK (8+)"


def _dotnet_major(line: str) -> int:
    try:
        return int(line.split(".", 1)[0])
    except ValueError:
        return -1


def process_group_options() -> dict[str, object]:
    if os.name == "nt":
        return {"creationflags": 0x00000200}
    return {"start_new_session": True}


async def terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=5)
        except (OSError, asyncio.TimeoutError):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
