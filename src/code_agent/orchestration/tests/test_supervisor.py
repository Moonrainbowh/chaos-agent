from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace

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


class ClosingRunner:
    def __init__(self):
        self.started = asyncio.Event()
        self.closing = asyncio.Event()
        self.allow_close = asyncio.Event()
        self.closed = False

    async def run(self, request, cancellation):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return ChildRunResult(
                request.run_id, RunStatus.CANCELLED, "partial",
                AgentUsage(17, 2, 1),
            )
        finally:
            self.closing.set()
            await self.allow_close.wait()
            self.closed = True


class ChildRunSupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_run_cleanup_waits_for_closer_without_cancelling_parent(self):
        runner = ClosingRunner()
        supervisor = ChildRunSupervisor(runner, BudgetLedger(ParentBudget()))
        parent_continued = asyncio.Event()

        async def parent():
            result = await supervisor.run(_request("direct"))
            parent_continued.set()
            return result

        caller = asyncio.create_task(parent())
        await runner.started.wait()
        self.assertEqual(await supervisor.cancel_all(), 1)
        await runner.closing.wait()
        waiting = asyncio.create_task(supervisor.wait_all())
        await asyncio.sleep(0)
        self.assertFalse(waiting.done())
        self.assertFalse(parent_continued.is_set())
        runner.allow_close.set()
        results = await waiting
        result = await caller
        self.assertTrue(runner.closed)
        self.assertTrue(parent_continued.is_set())
        self.assertEqual(results, (result,))
        self.assertEqual(result.status, RunStatus.CANCELLED)
        self.assertEqual(result.usage, AgentUsage(17, 2, 1))
        state = await supervisor._ledger.state()
        self.assertEqual(state.running_leases, 0)
        self.assertEqual(state.used, result.usage)

    async def test_timeout_stops_noncooperative_runner_and_preserves_usage(self):
        runner = ClosingRunner()
        supervisor = ChildRunSupervisor(runner, BudgetLedger(ParentBudget()))
        task = supervisor.start(replace(_request("timed"), active_seconds=1))
        await runner.started.wait()
        await asyncio.wait_for(runner.closing.wait(), 2)
        self.assertFalse(task.done())
        runner.allow_close.set()
        result = await task
        self.assertTrue(runner.closed)
        self.assertEqual(result.status, RunStatus.CANCELLED)
        self.assertEqual(result.error, "child active-time budget exceeded")
        self.assertEqual(result.usage.total_tokens, 17)

    async def test_caller_cancel_waits_for_child_cleanup_and_settles_returned_usage(self):
        runner = ClosingRunner()
        supervisor = ChildRunSupervisor(runner, BudgetLedger(ParentBudget()))
        caller = asyncio.create_task(supervisor.run(_request("caller")))
        await runner.started.wait()
        caller.cancel()
        await runner.closing.wait()
        self.assertFalse(caller.done())
        caller.cancel()
        await asyncio.sleep(0)
        self.assertFalse(caller.done())
        runner.allow_close.set()
        result = await caller
        self.assertTrue(runner.closed)
        self.assertEqual(result.usage.total_tokens, 17)
        self.assertEqual(result.status, RunStatus.CANCELLED)

    async def test_cancel_all_before_task_first_step_cleans_pending_run(self):
        runner = ClosingRunner()
        supervisor = ChildRunSupervisor(runner, BudgetLedger(ParentBudget()))
        task = supervisor.start(_request("pending"))
        self.assertEqual(await supervisor.cancel_all(), 1)
        results = await supervisor.wait_all()
        self.assertEqual(results, (await task,))
        self.assertEqual(results[0].status, RunStatus.CANCELLED)
        self.assertFalse(runner.started.is_set())
        self.assertEqual((await supervisor._ledger.state()).running_leases, 0)

    async def test_queued_child_cancellation_does_not_wait_for_running_sibling(self):
        runner = TrackingRunner()
        supervisor = ChildRunSupervisor(
            runner, BudgetLedger(ParentBudget(max_concurrency=1))
        )
        started = asyncio.Event()
        supervisor.subscribe(
            lambda view: started.set() if view.status is RunStatus.RUNNING else None
        )
        first = supervisor.start(_request("running"))
        await started.wait()
        second = supervisor.start(_request("queued"))
        await supervisor.cancel("queued", "stop queued")
        result = await asyncio.wait_for(second, 1)
        self.assertEqual(result.status, RunStatus.CANCELLED)
        self.assertFalse(first.done())
        runner.release.set()
        await first

    async def test_started_task_cancel_preserves_closer_and_returned_usage(self):
        runner = ClosingRunner()
        supervisor = ChildRunSupervisor(runner, BudgetLedger(ParentBudget()))
        task = supervisor.start(_request("external"))
        await runner.started.wait()
        task.cancel()
        await runner.closing.wait()
        self.assertFalse(task.done())
        runner.allow_close.set()
        result = await task
        self.assertTrue(runner.closed)
        self.assertEqual(result.usage.total_tokens, 17)
        self.assertEqual(result.status, RunStatus.CANCELLED)

    async def test_usage_overrun_is_reported_without_clipping_actual_usage(self):
        class OverrunRunner:
            async def run(self, request, cancellation):
                return ChildRunResult(
                    request.run_id, RunStatus.COMPLETED, "done", AgentUsage(101, 5, 31)
                )

        supervisor = ChildRunSupervisor(OverrunRunner(), BudgetLedger(ParentBudget()))
        result = await supervisor.run(_request("overrun"))
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.usage, AgentUsage(101, 5, 31))

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
