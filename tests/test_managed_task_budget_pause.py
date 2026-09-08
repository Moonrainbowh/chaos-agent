import tempfile
import unittest
from pathlib import Path
from code_agent.capabilities import CapabilityStrategy
from code_agent.core.engine import AgentEngine
from code_agent.core.events import EventKind
from code_agent.core.models import ContextBundle
from code_agent.core.task import TaskContract, TaskAuthorization, TaskStatus
from code_agent.core.limits import EngineLimits
from code_agent.context_windows.client import BudgetedWindowClient
from code_agent.context_windows.counting import PromptTokenCounter
from code_agent.context_windows.policy import WindowPolicy, ApiContextLimits
from code_agent.sessions.repository import SQLiteSessionRepository


class Context:
    async def build(self, request):
        return ContextBundle("system",request.messages)


class Actions:
    def tools(self):
        return ()


class ManagedPauseTests(unittest.IsolatedAsyncioTestCase):
    async def test_next_request_budget_denial_persists_paused_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=SQLiteSessionRepository(Path(tmp)/"sessions.db")
            thread=await repo.create_thread()
            task=await repo.create_task(thread,TaskContract("repair",TaskAuthorization.local_workspace(tmp)))
            task=await repo.transition_task(task.id,TaskStatus.RUNNING,"started")
            client=BudgetedWindowClient(object(),repo,lambda:thread,
                WindowPolicy(work_tokens=2000,safety_tokens=100,task_tokens=500),
                ApiContextLimits(4000,1000),PromptTokenCounter())
            engine=AgentEngine(client,Context(),Actions(),repo,limits=EngineLimits(10,0,1,500),
                model_name="test",capability_strategy=CapabilityStrategy.LEGACY)
            events=[e async for e in engine.run("continue",thread_id=thread,task=task)]
            self.assertIn(EventKind.TASK_PAUSED,[e.kind for e in events])
            self.assertEqual((await repo.load_task(task.id)).status,TaskStatus.PAUSED)
            self.assertEqual(await repo.context_records(thread,"usage"),())
