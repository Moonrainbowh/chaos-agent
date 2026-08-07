from __future__ import annotations

import asyncio
import inspect
import math
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from .models import Scenario, ScenarioPrompt, ScenarioResult, VerifierOracle
from .observation import HarnessObservation, manifest_digest, workspace_manifest
from .runner_evidence import combine_verifiers, infrastructure_failures
from .runner_types import ExecutionOutcome, RunData, make_run_data
from .sandbox import materialize_fixture_files, outside_workspace_paths, remove_tree, workspace_links
from .trace import TrustedTraceRecorder
from .verifier import CommandOutcome, SubprocessVerifier, VerifierExecutor, run_trusted_verifier


ScenarioExecutor = Callable[..., Awaitable[ScenarioResult]]


class ScenarioRunner:
    """Execute in a disposable workspace and retain only trusted observations."""

    def __init__(self, verifier_executor: VerifierExecutor | None = None) -> None:
        self._verify = verifier_executor or SubprocessVerifier()

    async def run(
        self,
        scenario: Scenario,
        execute: ScenarioExecutor,
    ) -> tuple[ScenarioResult, HarnessObservation]:
        if not isinstance(scenario, Scenario) or not callable(execute):
            raise TypeError("scenario and execute must be valid")
        temporary = Path(tempfile.mkdtemp(prefix="chaos-evaluation-"))
        workspace = temporary / "workspace"
        recorder = TrustedTraceRecorder()
        try:
            data = await self._run_in_sandbox(scenario, execute, temporary, workspace, recorder)
        except BaseException:
            await asyncio.to_thread(remove_tree, temporary)
            raise
        cleaned = data.termination_confirmed and await asyncio.to_thread(remove_tree, temporary)
        observation = _observation(data, workspace, cleaned, recorder)
        return replace(data.result, active_seconds=math.ceil(data.elapsed)), observation

    async def _run_in_sandbox(
        self,
        scenario: Scenario,
        execute: ScenarioExecutor,
        temporary: Path,
        workspace: Path,
        recorder: TrustedTraceRecorder,
    ) -> RunData:
        baseline, baseline_outcomes, clones_removed = await self._prepare_baseline(
            scenario,
            temporary,
            workspace,
        )
        if not clones_removed:
            raise RuntimeError("baseline verifier clones could not be removed safely")
        execution = await _execute_with_timeout(scenario, execute, workspace, recorder)
        final = await asyncio.to_thread(workspace_manifest, workspace)
        final_links = await asyncio.to_thread(workspace_links, workspace)
        outside = await asyncio.to_thread(outside_workspace_paths, temporary, workspace)
        final_outcomes, frozen = await self._final_verifiers(
            scenario,
            workspace,
            temporary,
            execution.termination_confirmed and not final_links and not outside,
        )
        trusted = combine_verifiers(
            scenario.expected.verifiers,
            baseline_outcomes,
            final_outcomes,
            manifest_digest(final),
        )
        infrastructure = infrastructure_failures(
            execution.infrastructure_failure,
            trusted,
            final_links,
        )
        return make_run_data(
            execution,
            baseline,
            final,
            trusted,
            outside,
            clones_removed,
            frozen,
            infrastructure,
        )

    async def _prepare_baseline(
        self,
        scenario: Scenario,
        temporary: Path,
        workspace: Path,
    ) -> tuple[dict[str, str], dict[str, CommandOutcome], bool]:
        await asyncio.to_thread(shutil.copytree, scenario.fixture_root, workspace, symlinks=True)
        await asyncio.to_thread(materialize_fixture_files, workspace, scenario.fixture_files)
        baseline_links = await asyncio.to_thread(workspace_links, workspace)
        if baseline_links:
            raise ValueError(f"fixture workspace contains links: {baseline_links}")
        baseline = await asyncio.to_thread(workspace_manifest, workspace)
        baseline_outcomes, clones_removed = await self._baseline_verifiers(
            scenario,
            workspace,
            temporary,
        )
        return baseline, baseline_outcomes, clones_removed

    async def _baseline_verifiers(
        self,
        scenario: Scenario,
        workspace: Path,
        temporary: Path,
    ) -> tuple[dict[str, CommandOutcome], bool]:
        root = temporary / "baseline-verifiers"
        root.mkdir()
        outcomes = await self._run_verifiers(workspace, root, scenario.expected.verifiers, "baseline")
        removed = await asyncio.to_thread(remove_tree, root)
        return outcomes, removed

    async def _final_verifiers(
        self,
        scenario: Scenario,
        workspace: Path,
        temporary: Path,
        terminated: bool,
    ) -> tuple[dict[str, CommandOutcome], bool]:
        if not terminated:
            return {}, False
        snapshot = temporary / "final-snapshot"
        await asyncio.to_thread(shutil.copytree, workspace, snapshot, symlinks=True)
        root = temporary / "final-verifiers"
        root.mkdir()
        outcomes = await self._run_verifiers(snapshot, root, scenario.expected.verifiers, "final")
        return outcomes, True

    async def _run_verifiers(
        self,
        source: Path,
        verifier_root: Path,
        oracles: tuple[VerifierOracle, ...],
        phase: str,
    ) -> dict[str, CommandOutcome]:
        outcomes: dict[str, CommandOutcome] = {}
        for oracle in oracles:
            outcomes[oracle.name] = await run_trusted_verifier(
                source,
                verifier_root,
                oracle,
                phase,
                self._verify,
            )
        return outcomes


async def _execute_with_timeout(
    scenario: Scenario,
    execute: ScenarioExecutor,
    workspace: Path,
    recorder: TrustedTraceRecorder,
) -> ExecutionOutcome:
    started = time.monotonic()
    isolation = getattr(execute, "isolation_mode", "in_process-cooperative")
    task = asyncio.create_task(_invoke_executor(execute, workspace, scenario.prompt(), recorder))
    done, _ = await asyncio.wait({task}, timeout=scenario.max_active_seconds)
    if task in done:
        return _completed_execution(task, started, isolation)
    task.cancel()
    grace = 6.0 if isolation == "subprocess-tree" else 0.25
    terminated = await _confirm_cancellation(task, grace)
    elapsed = time.monotonic() - started
    return ExecutionOutcome(ScenarioResult("timed_out"), elapsed, True, terminated, isolation)


async def _invoke_executor(
    execute: ScenarioExecutor,
    workspace: Path,
    prompt: ScenarioPrompt,
    recorder: TrustedTraceRecorder,
) -> ScenarioResult:
    arguments = (workspace, prompt, recorder) if _accepts_recorder(execute) else (workspace, prompt)
    result = await execute(*arguments)
    if not isinstance(result, ScenarioResult):
        raise TypeError("scenario executor must return ScenarioResult")
    return result


def _accepts_recorder(execute: ScenarioExecutor) -> bool:
    try:
        inspect.signature(execute).bind(Path("."), object(), TrustedTraceRecorder())
    except (TypeError, ValueError):
        return False
    return True


def _completed_execution(
    task: asyncio.Task[ScenarioResult],
    started: float,
    isolation: str,
) -> ExecutionOutcome:
    elapsed = time.monotonic() - started
    try:
        return ExecutionOutcome(task.result(), elapsed, False, True, isolation)
    except asyncio.CancelledError:
        return ExecutionOutcome(
            ScenarioResult("executor_failed"),
            elapsed,
            False,
            True,
            isolation,
            "executor failed: CancelledError",
        )
    except Exception as error:
        detail = str(error).replace("\n", " ")[:160]
        failure = f"executor failed: {type(error).__name__}: {detail}"
        return ExecutionOutcome(ScenarioResult("executor_failed"), elapsed, False, True, isolation, failure)


async def _confirm_cancellation(task: asyncio.Task[ScenarioResult], grace: float) -> bool:
    done, _ = await asyncio.wait({task}, timeout=grace)
    if task not in done:
        task.add_done_callback(_consume_task_exception)
        return False
    _consume_task_exception(task)
    return True


def _consume_task_exception(task: asyncio.Task[object]) -> None:
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


def _observation(
    data: RunData,
    workspace: Path,
    cleaned: bool,
    recorder: TrustedTraceRecorder,
) -> HarnessObservation:
    return HarnessObservation(
        baseline_manifest=data.baseline,
        final_manifest=data.final,
        verifier_results=data.verifiers,
        elapsed_seconds=data.elapsed,
        timed_out=data.timed_out,
        workspace_path=workspace,
        workspace_removed=cleaned,
        trace=recorder.snapshot(),
        outside_workspace_paths=data.outside_paths,
        baseline_clones_removed_before_execution=data.baseline_removed,
        final_snapshot_frozen=data.final_frozen,
        isolation_mode=data.isolation_mode,
        termination_confirmed=data.termination_confirmed,
        infrastructure_failures=data.infrastructure_failures,
    )
