from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from collections.abc import AsyncIterator, Sequence
from io import StringIO
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent_win.app import RootActionDispatcher, _session_path, create_application  # noqa: E402
from code_agent_win import agent_modes  # noqa: E402
from code_agent_win.cli import _split_global_options, _split_mode_option, run  # noqa: E402
from code_agent.core.cancellation import CancellationError, CancellationToken  # noqa: E402
from code_agent.core.engine import AgentEngine  # noqa: E402
from code_agent.core.events import EventKind  # noqa: E402
from code_agent.core.models import ActionRequest  # noqa: E402
from code_agent.core.models import ModelEvent, ModelEventKind, ToolCall, ToolDefinition  # noqa: E402
from code_agent.core.task import TaskStatus  # noqa: E402
from code_agent.interfaces.controller import AgentController  # noqa: E402
from code_agent.interfaces.task_controller import ForegroundTaskController  # noqa: E402
from code_agent.runtime.models import CommandResult, TerminationReason  # noqa: E402
from code_agent.context.builder import WorkspaceContextBuilder  # noqa: E402
from code_agent.context.compaction import DeterministicCompactor  # noqa: E402
from code_agent.context.models import ContextConfig  # noqa: E402
from code_agent.context.repo_index import RepoIndexService  # noqa: E402
from code_agent.context.repo_map import RepoMapBuilder  # noqa: E402
from code_agent.context.cache import RepoMapCache  # noqa: E402
from code_agent.context.rules import RuleLoader  # noqa: E402
from code_agent.interfaces.terminal_state import ApprovalBroker  # noqa: E402
from code_agent.policy.engine import ActionPolicy, PolicyConfig  # noqa: E402
from code_agent.policy.models import ApprovalMode  # noqa: E402
from code_agent.orchestration.models import AgentDefinition, AgentRole  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402
from code_agent.workspace.edits import WorkspaceEditor  # noqa: E402
from code_agent.verification.python_adapter import PythonVerificationAdapter  # noqa: E402
from code_agent.verification.task_service import LedgerTaskVerificationService  # noqa: E402
from code_agent.config.loader import load_runtime_config  # noqa: E402


class FakeModel:
    def __init__(self, streams: Sequence[Sequence[ModelEvent]]) -> None:
        self.streams = list(streams)

    def stream(
        self, system_prompt: str, messages: object, tools: Sequence[ToolDefinition]
    ) -> AsyncIterator[ModelEvent]:
        stream = self.streams.pop(0)

        async def generate() -> AsyncIterator[ModelEvent]:
            for event in stream:
                yield event

        return generate()


class CliFailureTests(unittest.IsolatedAsyncioTestCase):
    def test_global_options_are_removed_before_command_parsing(self) -> None:
        profile, model, command = _split_global_options(("--profile", "company", "ask", "inspect", "--model", "fast"))

        self.assertEqual((profile, model, command), ("company", "fast", ("ask", "inspect")))
        with self.assertRaises(ValueError):
            _split_global_options(("--model", "fast", "--model", "slow", "ask", "inspect"))

        mode, remaining = _split_mode_option(("ask", "inspect", "--mode", "ultra"))
        self.assertEqual((mode, remaining), ("ultra", ("ask", "inspect")))
        with self.assertRaises(ValueError):
            _split_mode_option(("--mode", "unbounded", "ask", "inspect"))

    async def test_cli_reports_safe_error_without_a_traceback(self) -> None:
        stderr = StringIO()

        with patch("code_agent_win.cli.create_application", side_effect=RuntimeError("secret detail")):
            with patch("sys.stderr", stderr):
                status = await run(("ask", "inspect"))

        self.assertEqual(status, 1)
        self.assertIn("RuntimeError", stderr.getvalue())
        self.assertNotIn("secret detail", stderr.getvalue())


class ApplicationConstructionTests(unittest.TestCase):
    def test_all_modes_share_complete_tools_and_plugin_contributions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = load_runtime_config(
                env={
                    "CHAOS_CONFIG": str(Path(temporary) / "missing.toml"),
                    "CHAOS_API": "responses",
                    "CHAOS_BASE_URL": "https://api.example.test",
                    "CHAOS_MODEL": "default-model",
                    "CHAOS_API_KEY_ENV": "KEY",
                }
            )
        profiles = {runtime.profile: runtime.profiles[0]}

        mode_env = {
            f"CHAOS_MODE_{mode.value.upper()}_PROFILE": runtime.profile
            for mode in agent_modes.AgentMode
        }
        with patch.dict("os.environ", mode_env):
            registry, _ = agent_modes.build_mode_registry(profiles, runtime.profile)
        definitions = registry.definitions()

        self.assertEqual(
            {definition.tool_names for definition in definitions},
            {agent_modes.ALL_TOOLS},
        )
        main_tools_for_mode = getattr(agent_modes, "main_tools_for_mode", None)
        self.assertIsNotNone(main_tools_for_mode)
        for definition in definitions:
            self.assertEqual(
                main_tools_for_mode(definition.tool_names, ("plugin.inspect",)),
                definition.tool_names + ("plugin.inspect",),
            )

    def test_tui_uses_the_session_repository_for_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runtime = load_runtime_config(env={
                "CHAOS_CONFIG": str(root / "missing.toml"), "CHAOS_API": "responses",
                "CHAOS_BASE_URL": "https://api.example.test", "CHAOS_MODEL": "test",
                "CHAOS_API_KEY_ENV": "KEY",
            })
            with patch("code_agent_win.app._model_client", return_value=object()):
                with patch(
                    "code_agent_win.app._session_path",
                    return_value=root / "sessions.sqlite3",
                ):
                    with patch("code_agent_win.app.load_runtime_config", return_value=runtime):
                        mode_env = {
                            f"CHAOS_MODE_{mode.value.upper()}_PROFILE": runtime.profile
                            for mode in agent_modes.AgentMode
                        }
                        with patch.dict("os.environ", mode_env):
                            application = create_application(root)
                            main_context = application.controller._engine._context._inner
                            child_agent = AgentDefinition(
                                "shared-index-child",
                                AgentRole.SEARCH,
                                application.mode,
                                "Inspect the repository.",
                                ("read_file",),
                            )
                            child_engine, _ = application.subagents._runner._factory(
                                child_agent
                            )
                            child_context = child_engine._context._inner

        self.assertIs(application.tui.sessions, application.tui.history)
        self.assertIs(application.tui.evidence, application.tui.sessions)
        self.assertEqual(application.mode.definition.mode.value, "medium")
        self.assertEqual(application.mode.model, "test")
        self.assertIsNotNone(application.plugins)
        self.assertIsNotNone(application.subagents)
        self.assertIsInstance(application.repo_index, RepoIndexService)
        self.assertIs(main_context.repo_map.index, application.repo_index)
        self.assertIs(child_context.repo_map.index, application.repo_index)


class ModeSwitchIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_write_refreshes_the_shared_repo_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "service.py"
            source.write_text("def before():\n    pass\n", encoding="utf-8")
            runtime = load_runtime_config(env={
                "CHAOS_CONFIG": str(root / "missing.toml"), "CHAOS_API": "responses",
                "CHAOS_BASE_URL": "https://api.example.test", "CHAOS_MODEL": "test",
                "CHAOS_API_KEY_ENV": "KEY", "CHAOS_APPROVAL_MODE": "auto",
            })
            mode_env = {
                f"CHAOS_MODE_{mode.value.upper()}_PROFILE": runtime.profile
                for mode in agent_modes.AgentMode
            }
            with patch("code_agent_win.app._model_client", side_effect=lambda _: object()):
                with patch("code_agent_win.app._session_path", return_value=root / "sessions.sqlite3"):
                    with patch("code_agent_win.app.load_runtime_config", return_value=runtime):
                        with patch.dict("os.environ", mode_env):
                            application = create_application(root)

            before = application.repo_index.snapshot_for_turn()
            result = await application.dispatcher.dispatch(
                ActionRequest(
                    "write-indexed-file",
                    "write_file",
                    {
                        "path": "service.py",
                        "content": "def after():\n    pass\n",
                    },
                ),
                CancellationToken(),
            )
            after = application.repo_index.snapshot_for_turn()

            self.assertFalse(result.is_error)
            self.assertGreater(after.generation, before.generation)
            self.assertEqual(
                tuple(symbol.name for symbol in after.entries[0].symbols),
                ("after",),
            )
            await application.aclose()

    async def test_tui_mode_switch_rebuilds_runner_and_updates_application_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runtime = load_runtime_config(env={
                "CHAOS_CONFIG": str(root / "missing.toml"), "CHAOS_API": "responses",
                "CHAOS_BASE_URL": "https://api.example.test", "CHAOS_MODEL": "test",
                "CHAOS_API_KEY_ENV": "KEY",
            })
            mode_env = {
                f"CHAOS_MODE_{mode.value.upper()}_PROFILE": runtime.profile
                for mode in agent_modes.AgentMode
            }
            with patch("code_agent_win.app._model_client", side_effect=lambda _: object()):
                with patch("code_agent_win.app._session_path", return_value=root / "sessions.sqlite3"):
                    with patch("code_agent_win.app.load_runtime_config", return_value=runtime):
                        with patch.dict("os.environ", mode_env):
                            application = create_application(root)

            previous_runner = application.controller._engine
            previous_repo_map = previous_runner._context._inner.repo_map
            selected = await application.tui.modes.use("high", idle=True)
            rebuilt_repo_map = application.controller._engine._context._inner.repo_map

            self.assertEqual(selected.name, "high")
            self.assertEqual(application.mode.definition.mode.value, "high")
            self.assertEqual(application.tui.modes.current.name, "high")
            self.assertIsNot(application.controller._engine, previous_runner)
            self.assertIs(rebuilt_repo_map.index, application.repo_index)
            self.assertIs(
                rebuilt_repo_map.view_cache,
                previous_repo_map.view_cache,
            )
            await application.aclose()

    def test_session_path_copies_the_legacy_data_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "code-agent" / "sessions.sqlite3"
            legacy.parent.mkdir()
            import sqlite3
            connection = sqlite3.connect(legacy)
            try:
                connection.execute("CREATE TABLE threads (id TEXT)")
                connection.commit()
            finally:
                connection.close()

            with patch("code_agent_win.app.os.getenv", return_value=str(root)):
                current = _session_path()

            self.assertEqual(current, root / "chaos-agent" / "sessions.sqlite3")
            self.assertTrue(current.exists())
            self.assertTrue(legacy.exists())

    def test_session_path_uses_the_chaos_agent_directory_for_new_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            with patch("code_agent_win.app.os.getenv", return_value=str(root)):
                self.assertEqual(_session_path(), root / "chaos-agent" / "sessions.sqlite3")


class FullStackTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_model_read_action_round_trips_through_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "note.txt").write_bytes(b"hello\n")
            guard = WorkspacePathGuard(root)
            files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
            config = ContextConfig(root, root, "System", repo_scan=100)
            context = WorkspaceContextBuilder(
                config,
                RuleLoader(guard, files, config),
                RepoMapBuilder(files, config),
                DeterministicCompactor(config),
            )
            dispatcher = RootActionDispatcher(
                files,
                WorkspaceEditor(guard),
                ActionPolicy(PolicyConfig(ApprovalMode.AUTO, workspace_root=root)),
                ApprovalBroker(),
            )
            call = ToolCall("call-1", "read_file", {"path": "note.txt"})
            model = FakeModel(
                (
                    (
                        ModelEvent(ModelEventKind.TOOL_CALL, tool_call=call),
                        ModelEvent(ModelEventKind.COMPLETED),
                    ),
                    (
                        ModelEvent(ModelEventKind.TEXT_DELTA, text="read complete"),
                        ModelEvent(ModelEventKind.COMPLETED),
                    ),
                )
            )
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            engine = AgentEngine(model, context, dispatcher, sessions)

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
            dispatcher = _task_dispatcher(root, runtime)
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
            controller = ForegroundTaskController(
                AgentController(AgentEngine(
                    model,
                    _task_context(root),
                    dispatcher,
                    sessions,
                    verification=LedgerTaskVerificationService(root, sessions),
                )),
                sessions,
                root,
            )
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
            controller = ForegroundTaskController(
                AgentController(AgentEngine(
                    model,
                    _task_context(root),
                    _task_dispatcher(root, runtime),
                    sessions,
                    verification=LedgerTaskVerificationService(root, sessions),
                )),
                sessions,
                root,
            )
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
            model = FakeModel(((
                ModelEvent(ModelEventKind.TEXT_DELTA, text="done"),
                ModelEvent(ModelEventKind.COMPLETED),
            ),))
            controller = ForegroundTaskController(
                AgentController(AgentEngine(
                    model,
                    _task_context(root),
                    _task_dispatcher(root, runtime),
                    sessions,
                    verification=LedgerTaskVerificationService(root, sessions),
                )),
                sessions,
                root,
            )
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
            dispatcher = _task_dispatcher(root, runtime)
            model = FakeModel(((
                ModelEvent(ModelEventKind.TOOL_CALL, tool_call=ToolCall("install", "run_command", {"command": "pip install package"})),
                ModelEvent(ModelEventKind.COMPLETED),
            ),))
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            controller = ForegroundTaskController(
                AgentController(AgentEngine(model, _task_context(root), dispatcher, sessions)),
                sessions,
                root,
            )
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
            first_controller = ForegroundTaskController(
                AgentController(AgentEngine(first_model, _task_context(root), _task_dispatcher(root, first_runtime), sessions)),
                sessions,
                root,
            )
            task = await first_controller.start("run tests")
            running = asyncio.create_task(_collect_events(first_controller.events(task.id)))
            await first_runtime.started.wait()
            await first_controller.pause(task.id, "terminal closed")
            await running

            resumed_runtime = _RecordingRuntime(())
            resumed = ForegroundTaskController(
                AgentController(AgentEngine(
                    FakeModel(((ModelEvent(ModelEventKind.TEXT_DELTA, text="rechecked"), ModelEvent(ModelEventKind.COMPLETED)),)),
                    _task_context(root), _task_dispatcher(root, resumed_runtime), SQLiteSessionRepository(database),
                )),
                SQLiteSessionRepository(database),
                root,
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
        config,
        RuleLoader(guard, files, config),
        RepoMapBuilder(files, config),
        DeterministicCompactor(config),
    )


def _task_dispatcher(root: Path, runtime: object) -> RootActionDispatcher:
    guard = WorkspacePathGuard(root)
    files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
    return RootActionDispatcher(
        files,
        WorkspaceEditor(guard),
        ActionPolicy(PolicyConfig(ApprovalMode.AUTO, workspace_root=root)),
        ApprovalBroker(),
        runtime=runtime,  # type: ignore[arg-type]
        verification=PythonVerificationAdapter(root),
    )


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
            reason=TerminationReason.EXITED, stdout=b"", stderr=b"test failure" if returncode else b"",
            duration_s=0, truncated=False, cwd=".",
        )


class _BlockingRuntime:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.commands: list[str] = []

    async def run(self, spec: object, cancellation: CancellationToken, sink: object) -> CommandResult:
        command = " ".join(getattr(spec, "argv") or ())
        self.commands.append(command)
        self.started.set()
        await cancellation.wait_async()
        raise CancellationError(cancellation.reason)


async def _collect_events(events: AsyncIterator[object]) -> list[object]:
    return [event async for event in events]


if __name__ == "__main__":
    unittest.main()
