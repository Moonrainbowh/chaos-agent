import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from code_agent.context_windows.client import BudgetedWindowClient
from code_agent.context_windows.counting import PromptTokenCounter
from code_agent.context_windows.policy import ApiContextLimits, WindowPolicy
from code_agent.context_windows.tools import WindowToolService
from code_agent.core.models import Message, Usage, ModelEvent, ModelEventKind, ActionRequest
from code_agent.core.cancellation import CancellationToken
from code_agent.core.errors import EngineLimitError
from code_agent.sessions.repository import SQLiteSessionRepository


class FakeClient:
    calls = 0
    def __init__(self, usage=True, fail=False):
        self.usage, self.fail = usage, fail
    async def stream(self, system, messages, tools):
        self.calls += 1
        if self.fail:
            raise RuntimeError("network interrupted")
        if self.usage:
            yield ModelEvent(ModelEventKind.USAGE, usage=Usage(100, 20, 50))
        yield ModelEvent(ModelEventKind.COMPLETED)


class BudgetClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = SQLiteSessionRepository(Path(self.tmp.name)/"session.db")
        self.thread = await self.repo.create_thread()
    async def asyncTearDown(self):
        self.tmp.cleanup()
    def client(self, raw, task=5000):
        return BudgetedWindowClient(raw, self.repo, lambda:self.thread,
            WindowPolicy(work_tokens=2000, safety_tokens=100, task_tokens=task),
            ApiContextLimits(4000,1000), PromptTokenCounter())
    async def consume(self, client):
        return [event async for event in client.stream("system", (Message("user","go"),), ())]

    async def test_cache_is_subset_and_main_plus_handoff_use_same_ledger(self):
        client = self.client(FakeClient())
        await self.consume(client)
        _ = [e async for e in client.stream_for("handoff", "summarize", (Message("user","history"),), ())]
        rows = await self.repo.context_records(self.thread,"usage")
        self.assertEqual([r["purpose"] for r in rows], ["main","handoff"])
        self.assertEqual(sum(r["charged"] for r in rows),240)

    async def test_missing_usage_retains_reservation_and_is_explicit_failure(self):
        with self.assertRaisesRegex(RuntimeError,"omitted token usage"):
            await self.consume(self.client(FakeClient(usage=False)))
        rows = await self.repo.context_records(self.thread,"usage")
        self.assertEqual(rows[0]["status"],"pending")
        self.assertGreater(rows[0]["reserved"],1000)

    async def test_network_interruption_is_not_free(self):
        with self.assertRaisesRegex(RuntimeError,"interrupted"):
            await self.consume(self.client(FakeClient(fail=True)))
        self.assertEqual((await self.repo.context_records(self.thread,"usage"))[0]["status"],"pending")

    async def test_budget_denial_happens_before_provider_call(self):
        raw=FakeClient()
        with self.assertRaises(EngineLimitError):
            await self.consume(self.client(raw,task=500))
        self.assertEqual(raw.calls,0)

    async def test_restart_cannot_increase_frozen_budget(self):
        identifier=await self.repo.reserve_context_call(self.thread,"first",500,600,"main")
        await self.repo.settle_context_call(self.thread,identifier,Usage(490,10),490)
        with self.assertRaisesRegex(ValueError,"budget"):
            await self.repo.reserve_context_call(self.thread,"next",200,5000000,"main")

    async def test_long_history_can_be_read_beyond_first_fragment(self):
        message=Message("user","a"*20000+"IMPORTANT_TAIL")
        await self.repo.append_message(self.thread,message)
        service=WindowToolService(self.repo,lambda:self.thread)
        first=await service.dispatch(ActionRequest("r1","context_history",{"operation":"read"}),CancellationToken())
        cursor=first.output["next"]
        second=await service.dispatch(ActionRequest("r2","context_history",{"operation":"read",**dict(cursor)}),CancellationToken())
        self.assertIn("IMPORTANT_TAIL",second.output["fragment"])
