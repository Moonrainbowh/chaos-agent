import asyncio
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from code_agent.context_windows.policy import ApiContextLimits, WindowPolicy
from code_agent.context_windows.counting import PromptTokenCounter
from code_agent.context_windows.builder import WindowContextBuilder
from code_agent.context_windows.client import BudgetedWindowClient
from code_agent.context_windows.tools import WindowToolService
from code_agent.core.models import Message, ContextBundle, ToolCall, Usage, ModelEvent, ModelEventKind, ActionRequest
from code_agent.core.context_request import ContextRequest
from code_agent.core.cancellation import CancellationToken
from code_agent.core.task_state import TaskState
from code_agent.sessions.repository import SQLiteSessionRepository


class Prefix:
    calls = 0
    async def build(self, request):
        self.calls += 1
        return ContextBundle("Rules and late-added active skills", request.messages)


class Handoff:
    calls = 0
    async def write(self, records, previous, cancellation):
        self.calls += 1
        return "Preserve original requirements; verify files before reusing claims."


class WindowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sessions.db"
        self.repo = SQLiteSessionRepository(self.path)
        self.thread = await self.repo.create_thread()
        self.policy = WindowPolicy(work_tokens=4000, safety_tokens=100, keep_groups=2, handoff_tokens=500)
        self.limits = ApiContextLimits(9000, 1000)
        self.counter = PromptTokenCounter()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def fill(self):
        await self.repo.append_message(self.thread, Message("user", "Keep exact units and repair the regression."))
        for i in range(8):
            await self.repo.append_message(self.thread, Message("assistant", tool_calls=(ToolCall(f"c{i}", "read_file", {"path": "contract.py"}),)))
            await self.repo.append_message(self.thread, Message("tool", "evidence " * 45, tool_call_id=f"c{i}"))

    def request(self, revision=1):
        return ContextRequest(self.thread, revision, (), "", (), TaskState(), CancellationToken())

    async def test_rotation_restart_idempotency_and_source_preservation(self):
        await self.fill()
        prefix, handoff = Prefix(), Handoff()
        builder = WindowContextBuilder(prefix, self.repo, self.policy, self.limits, self.counter, handoff)
        before = await self.repo.load_messages(self.thread)
        bundle = await builder.build(self.request())
        from code_agent.context.measurements import prompt_estimate
        self.assertEqual(bundle.measurements["prompt_estimated_tokens"],
                         prompt_estimate(bundle.system_prompt, bundle.messages, ""))
        self.assertEqual(bundle.measurements["prompt_budget_tokens"], self.limits.input_cap(self.policy))
        self.assertEqual(bundle.measurements["window_number"], 1)
        self.assertEqual(before, await self.repo.load_messages(self.thread))
        self.assertEqual(prefix.calls, 1)
        self.assertIn("Keep exact units", str(bundle.messages))
        restarted = WindowContextBuilder(Prefix(), SQLiteSessionRepository(self.path), self.policy, self.limits, self.counter, handoff)
        repeated = await restarted.build(self.request())
        self.assertEqual(bundle.messages, repeated.messages)
        self.assertEqual(handoff.calls, 1)

    async def test_incomplete_tool_group_never_rotates(self):
        await self.fill()
        await self.repo.append_message(self.thread, Message("assistant", tool_calls=(ToolCall("pending", "read_file", {}),)))
        builder = WindowContextBuilder(Prefix(), self.repo, self.policy, self.limits, self.counter, Handoff())
        with self.assertRaisesRegex(ValueError, "unfinished"):
            await builder.build(self.request())
        self.assertEqual(await self.repo.context_records(self.thread, "window"), ())

    async def test_atomic_window_cas_and_note_isolation(self):
        await self.repo.append_context_record(self.thread, "window", "a", {"x": 1}, expected_tail=None)
        with self.assertRaisesRegex(ValueError, "concurrently"):
            await self.repo.append_context_record(self.thread, "window", "b", {"x": 2}, expected_tail=None)
        service = WindowToolService(self.repo, lambda: self.thread)
        request = ActionRequest("n1", "context_note", {"operation": "write", "text": "check stale contract"})
        self.assertFalse((await service.dispatch(request, CancellationToken())).is_error)
        await service.dispatch(request, CancellationToken())
        self.assertEqual(len(await self.repo.context_records(self.thread, "note")), 1)
        other = await self.repo.create_thread()
        self.assertEqual(await self.repo.context_records(other, "note"), ())

    async def test_budget_reservation_concurrency_and_unknown_usage(self):
        reservations = await asyncio.gather(*[
            self.repo.reserve_context_call(self.thread, str(i), 600, 1000, "main") for i in range(2)
        ], return_exceptions=True)
        self.assertEqual(sum(isinstance(r, ValueError) for r in reservations), 1)
        identifier = next(r for r in reservations if isinstance(r, str))
        await self.repo.settle_context_call(self.thread, identifier, Usage(100, 20, 50), 110)
        await self.repo.reserve_context_call(self.thread, "next", 600, 1000, "handoff")
        rows = await self.repo.context_records(self.thread, "usage")
        self.assertEqual(sum(r.get("charged", r["reserved"]) for r in rows), 720)

    async def test_final_prefix_is_counted_and_large_input_fails(self):
        await self.repo.append_message(self.thread, Message("user", "x" * 12000))
        builder = WindowContextBuilder(Prefix(), self.repo, self.policy, self.limits, self.counter, Handoff())
        with self.assertRaisesRegex(ValueError, "cannot fit"):
            await builder.build(self.request())

    def test_api_and_work_limits_are_separate_from_task_budget(self):
        policy = WindowPolicy()
        self.assertEqual(ApiContextLimits(400000, 128000).input_cap(policy), 256000)
        self.assertEqual(ApiContextLimits(200000, 32000).input_cap(policy), 152000)
        self.assertEqual(ApiContextLimits(1050000, 128000, 200000).input_cap(policy), 184000)
        self.assertEqual(policy.task_tokens, 5000000)
        self.assertEqual(ApiContextLimits(400000,128000).input_cap(replace(policy,work_tokens=128000)),128000)
        self.assertEqual(ApiContextLimits(400000,128000).input_cap(replace(policy,work_tokens=512000)),256000)

    async def test_internal_journal_is_not_a_workspace_checkpoint(self):
        await self.repo.append_context_record(self.thread,"note","n",{"text":"working"})
        self.assertEqual(await self.repo.list_checkpoints(self.thread),())
        with self.assertRaisesRegex(ValueError,"reserved"):
            await self.repo.create_checkpoint(self.thread,"context:usage")

    async def test_manual_request_is_queued_and_does_not_claim_a_window(self):
        builder=WindowContextBuilder(Prefix(),self.repo,self.policy,self.limits,self.counter,Handoff())
        report=await builder.compact_context(self.thread,CancellationToken())
        self.assertEqual(report.status,"queued")
        self.assertEqual(await self.repo.context_records(self.thread,"window"),())


if __name__ == "__main__":
    unittest.main()
