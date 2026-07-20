from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.core.models import ActionRequest, ActionResult
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.policy.models import DecisionOutcome, PolicyDecision, RiskLevel
from code_agent.sessions.rewind_models import RewindMutationStatus
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent_win.action_dispatcher import RootActionDispatcher
from tests.test_rewind_capture import CaptureHarness, _blocking_observation, _prepared


class _Capture:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.error: BaseException | None = None
        self.cancel: CancellationToken | None = None

    async def apply_edit(self, context: object, request: ActionRequest, plan: object) -> None:
        self.calls.append(("edit", request.name))
        if self.error is not None:
            raise self.error

    async def record_gap(self, context: object, request: ActionRequest, reason: str) -> None:
        self.calls.append(("gap", request.name))
        if self.cancel is not None:
            self.cancel.cancel("after gap")
        if self.error is not None:
            raise self.error


class _Plugins:
    def __init__(self, target: str, risk: str) -> None:
        self.target, self.risk = target, risk

    def targets(self) -> dict[str, str]:
        return {"plugin.demo": self.target}

    def risk_map(self) -> dict[str, str]:
        return {"plugin.demo": self.risk}

    def definitions(self) -> tuple[object, ...]:
        return ()


class _Mcp:
    def __init__(self, risk: str) -> None:
        self.risk = risk
        self.called = False

    def definitions(self) -> tuple[object, ...]:
        return ()

    def risks(self) -> dict[str, str]:
        return {"mcp.demo.tool": self.risk}

    async def call(self, name: str, arguments: object) -> str:
        self.called = True
        return "ok"


class _Subagents:
    async def dispatch(self, request: ActionRequest, cancellation: object) -> ActionResult:
        return ActionResult(request.id, request.name, {"ok": True})


class _AllowPolicy:
    def evaluate(self, request: object, authorization: object = None) -> PolicyDecision:
        return PolicyDecision(DecisionOutcome.ALLOW, RiskLevel.LOW, "test allow")


class DispatcherCaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "note.txt").write_text("before", encoding="utf-8")
        self.guard = WorkspacePathGuard(self.root)
        self.capture = _Capture()
        self.context = ActionExecutionContext("owner", "origin", "request")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def dispatcher(self, **kwargs: object) -> RootActionDispatcher:
        return RootActionDispatcher(
            WorkspaceFiles(self.guard, IgnoreRules.from_workspace(self.root)),
            WorkspaceEditor(self.guard),
            _AllowPolicy(),  # type: ignore[arg-type]
            ApprovalBroker(),
            capture=self.capture,
            **{key: value for key, value in kwargs.items() if key != "risk"},
        )

    async def dispatch(self, dispatcher: RootActionDispatcher, request: ActionRequest):
        self.context = ActionExecutionContext("owner", "origin", request.id)
        return await dispatcher.dispatch(
            request, CancellationToken(), execution_context=self.context
        )

    async def test_plugin_alias_uses_translated_typed_target(self) -> None:
        result = await self.dispatch(
            self.dispatcher(plugins=_Plugins("write_file", "write")),
            ActionRequest("request", "plugin.demo", {"path": "note.txt", "content": "after"}),
        )
        self.assertFalse(result.is_error)
        self.assertEqual(self.capture.calls, [("edit", "write_file")])

    async def test_write_and_critical_mcp_record_gap_before_call(self) -> None:
        for risk in ("write", "critical"):
            with self.subTest(risk=risk):
                self.capture.calls.clear()
                mcp = _Mcp(risk)
                await self.dispatch(
                    self.dispatcher(mcp=mcp, risk=risk),
                    ActionRequest("request", "mcp.demo.tool", {}),
                )
                self.assertEqual(self.capture.calls, [("gap", "mcp.demo.tool")])
                self.assertTrue(mcp.called)

    async def test_write_and_critical_untyped_plugin_record_gap_before_call(self) -> None:
        dispatcher = self.dispatcher(plugins=_Plugins("read_file", "critical"))
        await self.dispatch(
            dispatcher,
            ActionRequest("request", "plugin.demo", {"path": "note.txt"}),
        )
        self.assertEqual(self.capture.calls, [("gap", "read_file")])

    async def test_read_only_mcp_and_plugin_create_no_gap(self) -> None:
        await self.dispatch(
            self.dispatcher(plugins=_Plugins("read_file", "read")),
            ActionRequest("request", "plugin.demo", {"path": "note.txt"}),
        )
        mcp = _Mcp("read")
        await self.dispatch(
            self.dispatcher(mcp=mcp, risk="read"),
            ActionRequest("request", "mcp.demo.tool", {}),
        )
        self.assertEqual(self.capture.calls, [])
        self.assertTrue(mcp.called)

    async def test_delegate_parent_creates_no_mutation_or_gap(self) -> None:
        await self.dispatch(
            self.dispatcher(subagents=_Subagents()),
            ActionRequest("request", "delegate_agent", {
                "objective": "review", "role": "review",
            }),
        )
        self.assertEqual(self.capture.calls, [])

    async def test_gap_failure_prevents_unknown_writer_call(self) -> None:
        self.capture.error = RuntimeError("gap")
        mcp = _Mcp("write")
        result = await self.dispatch(
            self.dispatcher(mcp=mcp, risk="write"),
            ActionRequest("request", "mcp.demo.tool", {}),
        )
        self.assertTrue(result.is_error)
        self.assertFalse(mcp.called)

    async def test_cancellation_is_not_converted_to_tool_error(self) -> None:
        self.capture.error = CancellationError("cancelled")
        with self.assertRaises(CancellationError):
            await self.dispatch(
                self.dispatcher(mcp=_Mcp("write"), risk="write"),
                ActionRequest("request", "mcp.demo.tool", {}),
            )

    async def test_post_gap_cancellation_prevents_direct_and_plugin_mcp_call(self) -> None:
        for plugins in (None, _Plugins("mcp.demo.tool", "write")):
            with self.subTest(plugin=plugins is not None):
                token, mcp = CancellationToken(), _Mcp("write")
                self.capture.cancel = token
                name = "plugin.demo" if plugins is not None else "mcp.demo.tool"
                request = ActionRequest("request", name, {})
                with self.assertRaises(CancellationError):
                    await self.dispatcher(mcp=mcp, plugins=plugins).dispatch(
                        request, token, execution_context=self.context
                    )
                self.assertFalse(mcp.called)
                self.capture.calls.clear()


class CaptureCancellationTests(CaptureHarness, unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.build()

    async def test_async_cancellation_waits_for_reconciliation_and_release(self) -> None:
        started, finish = threading.Event(), threading.Event()

        def apply(plan: object) -> None:
            self.calls.append("editor.apply")
            started.set()
            finish.wait(2)

        self.editor.apply = apply
        self.observed = _prepared().after
        with self.patches():
            task = asyncio.create_task(
                self.coordinator.apply_edit(self.context, self.request, self.plan)
            )
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            task.cancel()
            finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIn("sessions.complete", self.calls)
        self.assertLess(self.calls.index("sessions.complete"), self.calls.index("gate.release"))

    async def test_cancelled_prepare_settles_before_gate_release(self) -> None:
        blocker = self.sessions.block["prepare"] = asyncio.Event()
        prepare_started = self.sessions.started.setdefault("prepare", asyncio.Event())
        started, finish, observe = _blocking_observation(
            self.calls, _prepared().before)
        with self.patches(), unittest.mock.patch(
            "code_agent_win.rewind_capture.observe_file_states", observe
        ):
            task = asyncio.create_task(
                self.coordinator.apply_edit(self.context, self.request, self.plan)
            )
            await asyncio.wait_for(prepare_started.wait(), 2)
            task.cancel()
            blocker.set()
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            task.cancel()
            finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertNotIn("editor.apply", self.calls)
        self.assertIn("sessions.abort", self.calls)
        self.assertIs(self.sessions.finished, RewindMutationStatus.ABORTED)
        self.assertLess(self.calls.index("sessions.abort"), self.calls.index("gate.release"))

    async def test_token_cancellation_after_write_preserves_completed_journal(self) -> None:
        token = CancellationToken()
        journal: list[str] = []

        class Capture:
            async def apply_edit(inner, context, request, plan):
                journal.append("completed")
                token.cancel("after write")

        dispatcher = self._dispatcher(Capture())
        dispatcher.invalidate_cache = lambda paths: journal.append("cache")
        request = ActionRequest(
            "request", "write_file", {"path": "note.txt", "content": "after"}
        )
        with self.assertRaises(CancellationError):
            await dispatcher.dispatch(
                request, token, execution_context=self.context
            )
        self.assertEqual(journal, ["completed", "cache"])

    async def test_cache_invalidates_only_after_completed_journal(self) -> None:
        events: list[str] = []

        class Capture:
            async def apply_edit(inner, context, request, plan):
                events.append("sessions.complete")

        dispatcher = self._dispatcher(Capture())
        dispatcher.invalidate_cache = lambda paths: events.append("cache.invalidate")
        result = await dispatcher.dispatch(
            ActionRequest(
                "request", "write_file",
                {"path": "note.txt", "content": "after"},
            ),
            CancellationToken(),
            execution_context=self.context,
        )
        self.assertFalse(result.is_error)
        self.assertEqual(events, ["sessions.complete", "cache.invalidate"])

    def _dispatcher(
        self, capture: object,
        invalidated: list[tuple[str, ...]] | None = None,
    ) -> RootActionDispatcher:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Path(temporary.name).resolve()
        (workspace / "note.txt").write_text("before", encoding="utf-8")
        guard = WorkspacePathGuard(workspace)
        return RootActionDispatcher(
            WorkspaceFiles(guard, IgnoreRules.from_workspace(workspace)),
            WorkspaceEditor(guard), _AllowPolicy(), ApprovalBroker(),
            capture=capture,
            invalidate_cache=(
                None if invalidated is None
                else lambda paths: invalidated.append(tuple(paths))
            ),
        )
