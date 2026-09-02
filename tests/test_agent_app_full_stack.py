from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.context.builder import WorkspaceContextBuilder  # noqa: E402
from code_agent.context.compaction import DeterministicCompactor  # noqa: E402
from code_agent.context.models import ContextConfig  # noqa: E402
from code_agent.context.repo_map import RepoMapBuilder  # noqa: E402
from code_agent.context.rules import RuleLoader  # noqa: E402
from code_agent.capabilities.catalog import CONTRACT_TOOL_NAME  # noqa: E402
from code_agent.core.cancellation import CancellationError, CancellationToken  # noqa: E402
from code_agent.core.engine import AgentEngine  # noqa: E402
from code_agent.core.events import EventKind  # noqa: E402
from code_agent.core.models import ModelEvent, ModelEventKind, ToolCall, ToolDefinition  # noqa: E402
from code_agent.core.task import TaskStatus  # noqa: E402
from code_agent.interfaces.controller import AgentController  # noqa: E402
from code_agent.interfaces.task_controller import ForegroundTaskController  # noqa: E402
from code_agent.interfaces.terminal_state import ApprovalBroker  # noqa: E402
from code_agent.policy.engine import ActionPolicy, PolicyConfig  # noqa: E402
from code_agent.policy.models import ApprovalMode  # noqa: E402
from code_agent.runtime.models import CommandResult, TerminationReason  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402
from code_agent.verification.python_adapter import PythonVerificationAdapter  # noqa: E402
from code_agent.verification.task_service import LedgerTaskVerificationService  # noqa: E402
from code_agent.workspace.edits import WorkspaceEditor  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402
from code_agent_win.app import RootActionDispatcher  # noqa: E402


class FakeModel:
    def __init__(self, streams: Sequence[Sequence[ModelEvent]]) -> None:
        self.streams = list(streams)
        self.contract_calls = 0

    def stream(
        self, system_prompt: str, messages: object, tools: Sequence[ToolDefinition]
    ) -> AsyncIterator[ModelEvent]:
        stream = self.streams[0]
        available = {tool.name for tool in tools}
        requested = next(
            (
                event.tool_call
                for event in stream
                if event.kind is ModelEventKind.TOOL_CALL
                and event.tool_call is not None
            ),
            None,
        )
        if (
            requested is not None
            and requested.name not in available
            and CONTRACT_TOOL_NAME in available
        ):
            self.contract_calls += 1
            contract = ToolCall(
                f"contract-{self.contract_calls}",
                CONTRACT_TOOL_NAME,
                {"name": requested.name},
            )
            stream = (
                ModelEvent(ModelEventKind.TOOL_CALL, tool_call=contract),
                ModelEvent(ModelEventKind.COMPLETED),
            )
        else:
            self.streams.pop(0)

        async def generate() -> AsyncIterator[ModelEvent]:
            for event in stream:
                yield event

        return generate()


class FullStackTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_model_read_action_round_trips_through_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "note.txt").write_bytes(b"hello\n")
            dispatcher = _task_dispatcher(root, None)
            call = ToolCall("call-1", "read_file", {"path": "note.txt"})
            model = FakeModel((
                (ModelEvent(ModelEventKind.TOOL_CALL, tool_call=call), ModelEvent(ModelEventKind.COMPLETED)),
                (ModelEvent(ModelEventKind.TEXT_DELTA, text="read complete"), ModelEvent(ModelEventKind.COMPLETED)),
            ))
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            engine = AgentEngine(model, _task_context(root), dispatcher, sessions)

            events = [event async for event in engine.run("read note")]

            thread_id = events[0].payload["thread_id"]
            messages = await sessions.load_messages(thread_id)
            self.assertEqual(events[-1].kind, EventKind.COMPLETED)
            self.assertEqual(messages[-1].content, "read complete")
            self.assertIn("hello", messages[-2].content)

    async def test_foreground_task_repairs_a_failed_test_then_checkpoints_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "note.txt").write_text("before\n", encoding="utf-8")
            runtime = _RecordingRuntime((1, 0))
            calls = (
                ToolCall("read", "read_file", {"path": "note.txt"}),
                ToolCall("write-1", "write_file", {"path": "note.txt", "content": "broken\n"}),
                ToolCall("test-1", "run_verification", {"kind": "python_unittest"}),
                ToolCall("write-2", "write_file", {"path": "note.txt", "content": "fixed\n"}),
                ToolCall("test-2", "run_verification", {"kind": "python_unittest"}),
            )
            model = FakeModel(tuple(
                (ModelEvent(ModelEventKind.TOOL_CALL, tool_call=call), ModelEvent(ModelEventKind.COMPLETED))
                for call in calls
            ) + ((ModelEvent(ModelEventKind.TEXT_DELTA, text="fixed and verified"), ModelEvent(ModelEventKind.COMPLETED)),))
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            controller = _task_controller(root, runtime, model, sessions)
            task = await controller.start("repair note")

            events = [event async for event in controller.events(task.id)]
            stored = await sessions.load_task(task.id)

            self.assertEqual(stored.status, TaskStatus.COMPLETED)
            self.assertEqual((root / "note.txt").read_text(encoding="utf-8"), "fixed\n")
            self.assertIn("note.txt", (await sessions.load_task_state(task.thread_id)).files_changed)
            self.assertGreaterEqual(len(await sessions.list_checkpoints(task.thread_id)), 3)
            self.assertEqual((await sessions.load_task_budget(task.id)).repair_cycles, 1)
            self.assertEqual(len(runtime.commands), 2)
            self.assertEqual(events[-1].kind, EventKind.COMPLETED)

    async def test_current_verification_evidence_expires_after_a_later_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "note.txt").write_text("before\n", encoding="utf-8")
            runtime = _RecordingRuntime((0,))
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            model = FakeModel((
                (ModelEvent(ModelEventKind.TOOL_CALL, tool_call=ToolCall("verify", "run_verification", {"kind": "python_unittest"})), ModelEvent(ModelEventKind.COMPLETED)),
                (ModelEvent(ModelEventKind.TOOL_CALL, tool_call=ToolCall("write", "write_file", {"path": "note.txt", "content": "after\n"})), ModelEvent(ModelEventKind.COMPLETED)),
                (ModelEvent(ModelEventKind.TEXT_DELTA, text="done"), ModelEvent(ModelEventKind.COMPLETED)),
            ))
            controller = _task_controller(root, runtime, model, sessions)
            task = await controller.start("verify then edit")

            events = [event async for event in controller.events(task.id)]

            self.assertEqual((await sessions.load_task(task.id)).status, TaskStatus.VERIFYING)
            self.assertNotIn(EventKind.COMPLETED, [event.kind for event in events])

    async def test_model_completion_automatically_runs_discovered_project_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "pyproject.toml").write_text("[project]\nname = 'demo'\nversion = '0.0.0'\n", encoding="utf-8")
            runtime = _RecordingRuntime((0,))
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            model = FakeModel(((ModelEvent(ModelEventKind.TEXT_DELTA, text="done"), ModelEvent(ModelEventKind.COMPLETED)),))
            controller = _task_controller(root, runtime, model, sessions)
            task = await controller.start("repair project")

            events = [event async for event in controller.events(task.id)]

            self.assertEqual((await sessions.load_task(task.id)).status, TaskStatus.COMPLETED)
            self.assertEqual(len(runtime.commands), 1)
            self.assertIn("-m unittest discover", runtime.commands[0])
            self.assertEqual(events[-1].kind, EventKind.COMPLETED)
            messages = await sessions.load_messages(task.thread_id)
            self.assertEqual([message.role for message in messages[-3:]], ["assistant", "assistant", "tool"])
            self.assertEqual(messages[-2].tool_calls[0].id, messages[-1].tool_call_id)
            self.assertEqual(model.streams, [])

    async def test_task_boundary_waits_for_decision_without_starting_network_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runtime = _RecordingRuntime(())
            model = FakeModel(((
                ModelEvent(ModelEventKind.TOOL_CALL, tool_call=ToolCall("install", "run_command", {"command": "pip install package"})),
                ModelEvent(ModelEventKind.COMPLETED),
            ),))
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            controller = _task_controller(root, runtime, model, sessions)
            task = await controller.start("install package")

            events = [event async for event in controller.events(task.id)]

            self.assertEqual((await sessions.load_task(task.id)).status, TaskStatus.WAITING_DECISION)
            self.assertEqual(runtime.commands, [])
            self.assertIn(EventKind.TASK_DECISION_REQUIRED, [event.kind for event in events])

    async def test_resume_never_replays_an_interrupted_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            database = root / "sessions.sqlite3"
            first_runtime = _BlockingRuntime()
            first_model = FakeModel(((
                ModelEvent(ModelEventKind.TOOL_CALL, tool_call=ToolCall("test", "run_verification", {"kind": "python_unittest"})),
                ModelEvent(ModelEventKind.COMPLETED),
            ),))
            sessions = SQLiteSessionRepository(database)
            first = _task_controller(
                root, first_runtime, first_model, sessions, with_verification=False
            )
            task = await first.start("run tests")
            running = asyncio.create_task(_collect_events(first.events(task.id)))
            await first_runtime.started.wait()
            await first.pause(task.id, "terminal closed")
            await running

            resumed_runtime = _RecordingRuntime(())
            resumed_model = FakeModel(((ModelEvent(ModelEventKind.TEXT_DELTA, text="rechecked"), ModelEvent(ModelEventKind.COMPLETED)),))
            resumed = _task_controller(
                root, resumed_runtime, resumed_model, SQLiteSessionRepository(database),
                with_verification=False,
            )
            events = [event async for event in resumed.resume(task.id, "recheck workspace safely")]

            self.assertEqual(len(first_runtime.commands), 1)
            self.assertIn("-m unittest discover -s .", first_runtime.commands[0])
            self.assertEqual(resumed_runtime.commands, [])
            self.assertEqual((await sessions.load_task(task.id)).status, TaskStatus.VERIFYING)
            self.assertGreaterEqual(len(await sessions.list_checkpoints(task.thread_id)), 2)
            self.assertNotEqual(events[-1].kind, EventKind.COMPLETED)


def _task_context(root: Path) -> WorkspaceContextBuilder:
    guard = WorkspacePathGuard(root)
    files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
    config = ContextConfig(root, root, "System", repo_scan=100)
    return WorkspaceContextBuilder(
        config, RuleLoader(guard, files, config), RepoMapBuilder(files, config),
        DeterministicCompactor(config),
    )


def _task_dispatcher(root: Path, runtime: object | None) -> RootActionDispatcher:
    guard = WorkspacePathGuard(root)
    files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
    keywords = {}
    if runtime is not None:
        keywords = {"runtime": runtime, "verification": PythonVerificationAdapter(root)}
    return RootActionDispatcher(
        files, WorkspaceEditor(guard),
        ActionPolicy(PolicyConfig(ApprovalMode.AUTO, workspace_root=root)),
        ApprovalBroker(), **keywords,
    )


def _task_controller(
    root: Path,
    runtime: object,
    model: FakeModel,
    sessions: SQLiteSessionRepository,
    *,
    with_verification: bool = True,
) -> ForegroundTaskController:
    keywords = {}
    if with_verification:
        keywords["verification"] = LedgerTaskVerificationService(root, sessions)
    engine = AgentEngine(
        model, _task_context(root), _task_dispatcher(root, runtime), sessions,
        **keywords,
    )
    return ForegroundTaskController(AgentController(engine), sessions, root)


class _RecordingRuntime:
    def __init__(self, returncodes: tuple[int, ...]) -> None:
        self.returncodes = list(returncodes)
        self.commands: list[str] = []

    async def run(self, spec: object, cancellation: object, sink: object) -> CommandResult:
        command = " ".join(getattr(spec, "argv") or ())
        self.commands.append(command)
        returncode = self.returncodes.pop(0)
        return CommandResult(
            argv=("powershell",), display_command=command, returncode=returncode,
            reason=TerminationReason.EXITED, stdout=b"",
            stderr=b"test failure" if returncode else b"", duration_s=0,
            truncated=False, cwd=".",
        )


class _BlockingRuntime:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.commands: list[str] = []

    async def run(
        self, spec: object, cancellation: CancellationToken, sink: object
    ) -> CommandResult:
        command = " ".join(getattr(spec, "argv") or ())
        self.commands.append(command)
        self.started.set()
        await cancellation.wait_async()
        raise CancellationError(cancellation.reason)


async def _collect_events(events: AsyncIterator[object]) -> list[object]:
    return [event async for event in events]


if __name__ == "__main__":
    unittest.main()
