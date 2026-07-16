from __future__ import annotations

import asyncio
import unittest

from code_agent.core.cancellation import CancellationToken
from code_agent.orchestration.budget import BudgetLedger, ParentBudget
from code_agent.orchestration.models import (
    AgentDefinition,
    AgentMode,
    AgentRole,
    AgentUsage,
    ChildRunRequest,
    ChildRunResult,
    RunStatus,
)
from code_agent.orchestration.modes import ModeRegistry, standard_mode_definitions
from code_agent.orchestration.supervisor import ChildRunSupervisor
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig


def _snapshot():
    provider = ProviderConfig(
        "https://example.test",
        "worker-model",
        ApiProtocol.RESPONSES,
        api_key_env="TEST_KEY",
    )
    profiles = {
        mode.value: ModelProfile(mode.value, provider, 200_000, 8_192)
        for mode in AgentMode
    }
    return ModeRegistry(
        standard_mode_definitions({mode: mode.value for mode in AgentMode})
    ).freeze("medium", profiles)


def _request(run_id: str, *, write: bool = False) -> ChildRunRequest:
    agent = AgentDefinition(
        f"worker-{run_id}",
        AgentRole.SUBAGENT,
        _snapshot(),
        "Complete the bounded child objective.",
        may_write=write,
    )
    return ChildRunRequest(
        "parent", "Inspect and report.", agent, 1, 100, 4, 30, run_id
    )


class TrackingRunner:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.active_writers = 0
        self.max_writers = 0
        self.release = asyncio.Event()

    async def run(self, request, cancellation):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if request.agent.may_write:
            self.active_writers += 1
            self.max_writers = max(self.max_writers, self.active_writers)
        try:
            while not self.release.is_set():
                if await cancellation.wait_async(0.01):
                    cancellation.raise_if_cancelled()
            return ChildRunResult(
                request.run_id,
                RunStatus.COMPLETED,
                f"completed {request.run_id}",
                AgentUsage(10, 1, 1),
            )
        finally:
            if request.agent.may_write:
                self.active_writers -= 1
            self.active -= 1


class FailingRunner:
    async def run(self, request, cancellation):
        raise RuntimeError("secret upstream text")


class SuccessfulRunner:
    async def run(self, request, cancellation):
        return ChildRunResult(
            request.run_id, RunStatus.COMPLETED, "done", AgentUsage(10, 1, 1)
        )


class ChildRunSupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_subscriber_observes_queued_running_and_terminal_states(self) -> None:
        observed = []
        supervisor = ChildRunSupervisor(
            SuccessfulRunner(), BudgetLedger(ParentBudget(max_children=2))
        )
        unsubscribe = supervisor.subscribe(observed.append)

        result = await supervisor.run(_request("observed"))
        unsubscribe()

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(
            [view.status for view in observed],
            [RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.COMPLETED],
        )
    async def test_readers_run_in_parallel(self) -> None:
        runner = TrackingRunner()
        supervisor = ChildRunSupervisor(
            runner, BudgetLedger(ParentBudget(4, 2, 2, 1_000, 20, 100))
        )
        first = supervisor.start(_request("first"))
        second = supervisor.start(_request("second"))
        await asyncio.sleep(0.03)

        self.assertEqual(runner.max_active, 2)
        runner.release.set()
        results = await asyncio.gather(first, second)
        self.assertTrue(all(result.status is RunStatus.COMPLETED for result in results))

    async def test_writers_are_serialized(self) -> None:
        runner = TrackingRunner()
        supervisor = ChildRunSupervisor(
            runner, BudgetLedger(ParentBudget(4, 2, 2, 1_000, 20, 100))
        )
        first = supervisor.start(_request("first", write=True))
        second = supervisor.start(_request("second", write=True))
        await asyncio.sleep(0.03)

        self.assertEqual(runner.max_writers, 1)
        runner.release.set()
        await asyncio.gather(first, second)
        self.assertEqual(runner.max_writers, 1)

    async def test_child_can_be_cancelled_and_view_is_updated(self) -> None:
        runner = TrackingRunner()
        supervisor = ChildRunSupervisor(
            runner, BudgetLedger(ParentBudget(2, 1, 1, 500, 10, 60))
        )
        task = supervisor.start(_request("child"))
        await asyncio.sleep(0.02)

        self.assertTrue(await supervisor.cancel("child", "user stopped child"))
        result = await task
        views = await supervisor.views()
        self.assertEqual(result.status, RunStatus.CANCELLED)
        self.assertEqual(views[0].status, RunStatus.CANCELLED)

    async def test_parent_cancellation_propagates(self) -> None:
        parent = CancellationToken()
        runner = TrackingRunner()
        supervisor = ChildRunSupervisor(
            runner,
            BudgetLedger(ParentBudget(2, 1, 1, 500, 10, 60)),
            parent,
        )
        task = supervisor.start(_request("child"))
        await asyncio.sleep(0.02)

        parent.cancel("parent stopped")
        result = await task
        self.assertEqual(result.status, RunStatus.CANCELLED)

    async def test_runner_errors_are_bounded_to_error_type(self) -> None:
        supervisor = ChildRunSupervisor(
            FailingRunner(), BudgetLedger(ParentBudget(2, 1, 1, 500, 10, 60))
        )

        result = await supervisor.run(_request("failed"))
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.error, "RuntimeError")


if __name__ == "__main__":
    unittest.main()
