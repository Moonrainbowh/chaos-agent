from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from .models import LifecycleEvent, ScenarioPrompt, ScenarioResult
from .trace import TrustedTraceRecorder
from .verifier import process_group_options, terminate_process_tree


@dataclass(frozen=True, kw_only=True)
class ProcessScenarioExecutor:
    """Run only a host-trusted event adapter, never a raw model command."""

    trusted_adapter_argv: tuple[str, ...]
    max_output_bytes: int = 1_048_576
    isolation_mode: str = "subprocess-tree"

    def __post_init__(self) -> None:
        argv = tuple(self.trusted_adapter_argv)
        if not argv or len(argv) > 32 or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("trusted adapter argv must contain bounded text")
        if not 1 <= self.max_output_bytes <= 8_388_608:
            raise ValueError("process executor output limit is invalid")
        object.__setattr__(self, "trusted_adapter_argv", argv)

    async def __call__(
        self,
        workspace: Path,
        prompt: ScenarioPrompt,
        recorder: TrustedTraceRecorder,
    ) -> ScenarioResult:
        argv = self.trusted_adapter_argv
        executable = shutil.which(argv[0])
        if executable is None:
            raise RuntimeError(f"missing trusted adapter executable: {argv[0]}")
        process = await asyncio.create_subprocess_exec(
            executable,
            *argv[1:],
            cwd=str(workspace),
            env=_environment(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=self.max_output_bytes + 1,
            **process_group_options(),
        )
        request = json.dumps(asdict(prompt), sort_keys=True).encode("utf-8")
        try:
            output = await _bounded_exchange(process, request, self.max_output_bytes)
        except asyncio.CancelledError:
            await _finish_terminated_process(process)
            raise
        except _OutputLimitExceeded as error:
            await _finish_terminated_process(process)
            raise RuntimeError("trusted adapter output exceeded its limit") from error
        if process.returncode != 0:
            raise RuntimeError(f"executor adapter exited with code {process.returncode}")
        return _decode_response(output, recorder)


class _OutputLimitExceeded(Exception):
    pass


async def _bounded_exchange(
    process: asyncio.subprocess.Process,
    request: bytes,
    limit: int,
) -> bytes:
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("trusted adapter pipes are unavailable")
    process.stdin.write(request)
    await process.stdin.drain()
    process.stdin.close()
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await process.stdout.read(min(65_536, limit - size + 1))
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise _OutputLimitExceeded
        chunks.append(chunk)
    await process.wait()
    return b"".join(chunks)


async def _finish_terminated_process(process: asyncio.subprocess.Process) -> None:
    drain_task = (
        asyncio.create_task(_discard_output(process.stdout))
        if process.stdout is not None
        else None
    )
    try:
        await terminate_process_tree(process)
    except BaseException:
        if drain_task is not None and not drain_task.done():
            drain_task.cancel()
        if drain_task is not None:
            await asyncio.gather(drain_task, return_exceptions=True)
        raise
    if drain_task is not None:
        await drain_task


async def _discard_output(stdout: asyncio.StreamReader) -> None:
    while await stdout.read(65_536):
        pass


def _decode_response(output: bytes, recorder: TrustedTraceRecorder) -> ScenarioResult:
    try:
        payload = json.loads(output.decode("utf-8"))
        result_data = payload["result"]
        trace_data = payload["trace"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError("executor adapter returned an invalid envelope") from error
    _record_trace(trace_data, recorder)
    return ScenarioResult(
        result_data["task_status"],
        policy_events=tuple(result_data.get("policy_events", ())),
        model_turns=result_data.get("model_turns", 0),
        tool_calls=result_data.get("tool_calls", 0),
        verifier_runs=tuple(result_data.get("verifier_runs", ())),
        evidence_complete=result_data.get("evidence_complete", False),
    )


def _record_trace(data: object, recorder: TrustedTraceRecorder) -> None:
    if not isinstance(data, dict):
        raise RuntimeError("executor adapter trace must be an object")
    recorder.record_status(data["task_status"])
    for policy in data.get("policy_events", ()):
        recorder.record_policy(policy)
    recorder.record_budget(
        model_turns=data["model_turns"],
        tool_calls=data["tool_calls"],
    )
    recorder.record_evidence_generation(data["evidence_generation"])
    for event in data.get("lifecycle_events", ()):
        recorder.record_lifecycle(LifecycleEvent(**event))
    recorder.seal()


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "DOTNET_CLI_TELEMETRY_OPTOUT": "1"})
    return environment
