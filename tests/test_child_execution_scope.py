"""Exercise the production child factory with real Sessions and workspace routing."""
import os
import asyncio
import threading
from types import SimpleNamespace
import tempfile
import unittest
from pathlib import Path

import psutil

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.limits import EngineLimits
from code_agent.core.models import ModelEvent, ModelEventKind, ToolCall, Usage
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from code_agent.orchestration.models import AgentDefinition, AgentRole, ChildRunRequest
from code_agent.orchestration.models import ChildRunResult, RunStatus
from code_agent.orchestration.supervisor import ChildRunSupervisor
from code_agent.orchestration.budget import BudgetLedger, ParentBudget
from tests.agent_app_test_support import _isolated_application
from chaos_agent.child_runner import EngineChildRunner
from tests.test_subagent_integration import _runtime


class ChildClient:
    def __init__(self):
        self.calls = 0
        self.closed = False

    async def stream(self, system, messages, tools):
        self.calls += 1
        if self.calls == 1:
            yield ModelEvent(ModelEventKind.TOOL_CALL,
                tool_call=ToolCall("child-read", "read_file", {"path": "scope.txt"}))
        else:
            yield ModelEvent(ModelEventKind.TEXT_DELTA, text="read finished")
        yield ModelEvent(ModelEventKind.USAGE, usage=Usage(10, 5))
        yield ModelEvent(ModelEventKind.COMPLETED)

    async def aclose(self):
        self.closed = True


class ChildExecutionScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_foreground_releases_owner_only_after_child_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            app, _, _ = _isolated_application(Path(temporary))
            started, cleaning, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
            task = await app.foreground_tasks.start("Read a file")
            agent = AgentDefinition("reader", AgentRole.REVIEW, app.mode, "read", ("read_file",))
            class HeldChild:
                async def run(self, objective, cancellation):
                    started.set()
                    await cancellation.wait_async()
                    yield AgentEvent(EventKind.CANCELLED)
            class HeldCloser:
                async def aclose(self):
                    cleaning.set()
                    await finish.wait()
            child_tasks = []
            async def ask(prompt, **options):
                supervisor = ChildRunSupervisor(EngineChildRunner(lambda *args: (HeldChild(), HeldCloser())),
                    BudgetLedger(ParentBudget()), options['cancellation'])
                app.subagents._supervisors[task.id] = supervisor
                child_tasks.append(asyncio.create_task(supervisor.run(
                    ChildRunRequest(task.id, "read", agent, 1, 100, 4, 30))))
                await started.wait()
                yield AgentEvent(EventKind.COMPLETED)
            original = app.foreground_tasks._controller
            app.foreground_tasks._controller = SimpleNamespace(ask=ask)
            async def consume():
                return [event async for event in app.foreground_tasks.events(task.id)]
            parent = asyncio.create_task(consume())
            try:
                barrier = asyncio.create_task(cleaning.wait())
                ready, _ = await asyncio.wait((parent, barrier), timeout=5,
                    return_when=asyncio.FIRST_COMPLETED)
                if parent in ready:
                    self.fail("parent exited before child cleanup: " + repr(await parent))
                self.assertIn(barrier, ready, "child cleanup did not begin")
                await barrier
                self.assertFalse(parent.done())
                self.assertIsNotNone((await app.sessions.recovery_checklist(task.id))['execution_owner'])
                finish.set()
                await asyncio.wait_for(parent, 5)
                self.assertIsNone((await app.sessions.recovery_checklist(task.id))['execution_owner'])
                self.assertEqual((await child_tasks[0]).status, RunStatus.CANCELLED)
            finally:
                finish.set()
                if not barrier.done():
                    barrier.cancel()
                await asyncio.gather(parent, *child_tasks, return_exceptions=True)
                app.foreground_tasks._controller = original
                await app.aclose()

    async def test_forced_cancellation_waits_for_threaded_action_and_closer(self):
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        writes = []
        class Engine:
            async def run(self, objective, cancellation):
                yield AgentEvent(EventKind.RUN_STARTED, {"thread_id": "child"})
                def worker():
                    loop.call_soon_threadsafe(entered.set)
                    release.wait(10)
                    writes.append("settled")
                await asyncio.to_thread(worker)
                yield AgentEvent(EventKind.CANCELLED, {"reason": "cancelled"})
        closer = ChildClient()
        registry, profiles = _runtime()
        agent = AgentDefinition("reader", AgentRole.REVIEW, registry.freeze("medium", profiles),
            "read", ("read_file",))
        runner = EngineChildRunner(lambda *args: (Engine(), closer))
        pending = asyncio.create_task(runner.run(
            ChildRunRequest("parent", "read", agent, 1, 100, 4, 30), CancellationToken()))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            pending.cancel()
            for _ in range(4):
                await asyncio.sleep(0)
            self.assertFalse(pending.done())
            self.assertFalse(closer.closed)
            release.set()
            result = await asyncio.wait_for(pending, 5)
            self.assertEqual(result.status.value, "cancelled")
            self.assertEqual(writes, ["settled"])
            self.assertTrue(closer.closed)
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)

    async def test_production_child_reads_frozen_root_and_charges_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            app, source, _ = _isolated_application(Path(temporary))
            authorized = Path(temporary) / "authorized"
            authorized.mkdir()
            (source / "scope.txt").write_text("WRONG SOURCE", encoding="utf-8")
            (authorized / "scope.txt").write_text("FROZEN ROOT", encoding="utf-8")
            client = ChildClient()
            runner = app.subagents._runner
            runner._factory.__self__._client_factory = lambda *args, **kwargs: client
            sessions = app.sessions
            owner = await sessions.create_thread()
            task = await sessions.create_task(owner, TaskContract("Read only",
                TaskAuthorization(str(authorized), allow_workspace_write=False, allow_local_execute=False)))
            await sessions.transition_task(task.id, TaskStatus.RUNNING)
            await sessions.get_or_create_task_budget(owner, "parent", EngineLimits(10, 10, 2, 200000))
            process = psutil.Process(os.getpid())
            await sessions.register_task_execution(task.id, "parent-instance", process.pid, process.create_time())
            binding = runner._thread_binding
            binding.bind("wrong-global-thread")
            runner.bind_execution_context(ActionExecutionContext(owner, owner, "delegate", task.id))
            agent = AgentDefinition("reader", AgentRole.REVIEW, app.mode, "Read only", ("read_file",))
            request = ChildRunRequest(task.id, "Read scope.txt", agent, 1, 100000, 4, 30)
            try:
                result = await runner.run(request, CancellationToken())
                self.assertTrue(client.closed)
                self.assertEqual(binding.current(), "wrong-global-thread")
                child = app.subagents._child_threads[request.run_id]
                self.assertEqual((await sessions.load_thread_relation(child)).parent_thread_id, owner)
                messages = await sessions.load_messages(child)
                tool_text = " ".join(m.content or "" for m in messages if m.role == "tool")
                self.assertIn("FROZEN ROOT", tool_text)
                self.assertNotIn("WRONG SOURCE", tool_text)
                self.assertEqual(result.usage.total_tokens, 30)
                self.assertTrue(result.usage_complete)
                budget = await sessions.load_task_budget(task.id)
                self.assertEqual(budget.input_tokens + budget.output_tokens, 30)
                self.assertEqual(budget.tool_calls, 1)
                self.assertEqual(budget.model_turns, 2)
                self.assertEqual((await sessions.load_task(task.id)).status, TaskStatus.RUNNING)
            finally:
                await sessions.release_task_execution(task.id, "parent-instance")
                await app.aclose()
