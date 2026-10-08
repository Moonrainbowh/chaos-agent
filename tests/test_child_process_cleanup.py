"""Real Windows Job cleanup through the production child factory and Core."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import psutil

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.limits import EngineLimits
from code_agent.core.models import ModelEvent, ModelEventKind, ToolCall, Usage
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from code_agent.orchestration.budget import BudgetLedger, ParentBudget
from code_agent.orchestration.models import AgentDefinition, AgentRole, ChildRunRequest, RunStatus
from code_agent.orchestration.supervisor import ChildRunSupervisor
from tests.agent_app_test_support import _isolated_application


GRANDCHILD = """import json, os, time
from pathlib import Path
import psutil
p = psutil.Process(os.getpid())
Path('grandchild.json').write_text(json.dumps([p.pid, p.create_time()]))
time.sleep(20)
Path('late-write.txt').write_text('unexpected late write')
"""
PARENT = """import json, os, subprocess, sys, time
from pathlib import Path
import psutil
p = psutil.Process(os.getpid())
Path('parent.json').write_text(json.dumps([p.pid, p.create_time()]))
subprocess.Popen([sys.executable, 'grandchild.py'], creationflags=subprocess.CREATE_NO_WINDOW)
time.sleep(30)
"""


def _same_process(identity):
    pid, created = identity
    try:
        process = psutil.Process(pid)
        return process if abs(process.create_time() - created) < 0.001 else None
    except psutil.NoSuchProcess:
        return None


class ProcessClient:
    def __init__(self):
        self.calls = 0
        self.closing = asyncio.Event()
        self.allow_close = asyncio.Event()
        self.closed = False

    async def stream(self, system, messages, tools):
        self.calls += 1
        if self.calls == 1:
            yield ModelEvent(ModelEventKind.TOOL_CALL, tool_call=ToolCall(
                "child-process", "run_process_v1", {
                    "program": sys.executable, "args": ["parent.py"],
                    "cwd": ".", "timeout_s": 30,
                }))
        else:
            yield ModelEvent(ModelEventKind.TEXT_DELTA, text="Process settled.")
        yield ModelEvent(ModelEventKind.USAGE, usage=Usage(10, 5))
        yield ModelEvent(ModelEventKind.COMPLETED)

    async def aclose(self):
        self.closing.set()
        await self.allow_close.wait()
        self.closed = True


@unittest.skipUnless(os.name == "nt", "real Windows Job/process tree required")
class ChildProcessCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_parent_cancel_kills_real_process_tree_before_owner_release(self):
        await self._check_cleanup(timeout=False)

    async def test_active_timeout_kills_real_process_tree_before_owner_release(self):
        await self._check_cleanup(timeout=True)

    async def _owner(self, sessions, task_id):
        return await sessions._database.read(lambda connection: connection.execute(
            "SELECT instance_id FROM task_executions WHERE task_id=?", (task_id,)
        ).fetchone())

    async def _wait_identities(self, root, pending):
        async def ready():
            while True:
                if pending.done():
                    self.fail(f"child exited before spawning tree: {pending.result()}")
                try:
                    return [json.loads((root / name).read_text())
                            for name in ("parent.json", "grandchild.json")]
                except (FileNotFoundError, json.JSONDecodeError):
                    await asyncio.sleep(0.02)
        return await asyncio.wait_for(ready(), 5)

    async def _check_cleanup(self, *, timeout):
        with tempfile.TemporaryDirectory() as temporary:
            app, root, _ = _isolated_application(Path(temporary))
            (root / "parent.py").write_text(PARENT, encoding="utf-8")
            (root / "grandchild.py").write_text(GRANDCHILD, encoding="utf-8")
            client = ProcessClient()
            runner = app.subagents._runner
            runner._factory.__self__._client_factory = lambda *args, **kwargs: client
            sessions = app.sessions
            thread = await sessions.create_thread()
            task = await sessions.create_task(thread, TaskContract(
                "Run a bounded local process", TaskAuthorization.local_workspace(str(root))))
            await sessions.transition_task(task.id, TaskStatus.RUNNING)
            await sessions.get_or_create_task_budget(thread, "parent", EngineLimits(10, 10, 2, 200000))
            process = psutil.Process(os.getpid())
            await sessions.register_task_execution(task.id, "process-parent", process.pid, process.create_time())
            binding = runner.bind_execution_context(ActionExecutionContext(thread, thread, "delegate", task.id))
            agent = AgentDefinition("process-worker", AgentRole.SUBAGENT, app.mode,
                "Run the local process", ("run_process_v1",), may_write=True)
            parent_cancel = CancellationToken()
            supervisor = ChildRunSupervisor(runner, BudgetLedger(ParentBudget()), parent_cancel)
            request = ChildRunRequest(task.id, "Run parent.py", agent, 1, 100000, 4,
                                      4 if timeout else 30)
            pending = supervisor.start(request)
            try:
                identities = await self._wait_identities(root, pending)
                self.assertTrue(all(_same_process(identity) is not None for identity in identities))
                ancestors = _same_process(identities[1]).parents()
                self.assertIn(identities[0][0], [process.pid for process in ancestors])
                tree_root = _same_process(identities[0])
                if tree_root.ppid() != os.getpid():
                    tree_root = tree_root.parent()  # Windows venv launcher.
                self.assertEqual(tree_root.ppid(), os.getpid())
                identities = [(process.pid, process.create_time())
                              for process in (tree_root, *tree_root.children(recursive=True))]
                if not timeout:
                    parent_cancel.cancel("parent process test cancelled")
                await asyncio.wait_for(client.closing.wait(), 8)
                self.assertFalse(pending.done())
                self.assertFalse(client.closed)
                self.assertEqual((await self._owner(sessions, task.id))[0], "process-parent")
                self.assertTrue(all(_same_process(identity) is None for identity in identities))
                self.assertFalse((root / "late-write.txt").exists())
                client.allow_close.set()
                results = await asyncio.wait_for(supervisor.wait_all(), 5)
                result = await pending
                self.assertEqual(results, (result,))
                self.assertEqual(result.status, RunStatus.CANCELLED)
                self.assertTrue(client.closed)
                self.assertEqual((await self._owner(sessions, task.id))[0], "process-parent")
                self.assertTrue(await sessions.release_task_execution(task.id, "process-parent"))
                self.assertIsNone(await self._owner(sessions, task.id))
                self.assertTrue(all(_same_process(identity) is None for identity in identities))
                self.assertFalse((root / "late-write.txt").exists())
                print("CHILD_PROCESS_CLEANUP " + json.dumps({
                    "trigger": "active_timeout" if timeout else "parent_cancel",
                    "identities_pid_create_time": identities,
                    "tree_dead_before_closer_finished": True,
                    "owner_held_during_closer": True, "closer_finished": client.closed,
                    "owner_released_after_wait_all": True, "late_write": False,
                }))
            finally:
                parent_cancel.cancel("test cleanup")
                client.allow_close.set()
                await asyncio.wait_for(asyncio.gather(pending, return_exceptions=True), 10)
                for name in ("grandchild.json", "parent.json"):
                    if (root / name).exists():
                        found = _same_process(json.loads((root / name).read_text()))
                        if found is not None:
                            found.kill()
                            await asyncio.to_thread(found.wait, 5)
                runner.reset_execution_context(binding)
                await sessions.release_task_execution(task.id, "process-parent")
                await app.aclose()
