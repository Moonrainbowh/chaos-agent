from __future__ import annotations

import asyncio
import contextlib
import hashlib
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent_win.app import RootActionDispatcher, _session_path, create_application  # noqa: E402
from code_agent_win import agent_modes  # noqa: E402
from code_agent_win.cli import _split_global_options, _split_mode_option, run  # noqa: E402
from code_agent.core.cancellation import CancellationError, CancellationToken  # noqa: E402
from code_agent.core.action_execution import ActionExecutionContext  # noqa: E402
from code_agent.core.engine import AgentEngine  # noqa: E402
from code_agent.core.events import EventKind  # noqa: E402
from code_agent.core.limits import EngineLimits  # noqa: E402
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
from code_agent.workspace.errors import WorkspaceError  # noqa: E402
from code_agent.verification.python_adapter import PythonVerificationAdapter  # noqa: E402
from code_agent.verification.task_service import LedgerTaskVerificationService  # noqa: E402
from code_agent.config.loader import load_runtime_config  # noqa: E402
from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core.models import ActionRequest  # noqa: E402
from code_agent.sessions.rewind_models import RewindBaseline  # noqa: E402
from code_agent.sessions.rewind_repository import RewindSessionRepository  # noqa: E402
from code_agent_win.app import (  # noqa: E402
    Application,
    RootActionDispatcher,
    _product_state_root,
    _session_path,
    create_application,
)
from code_agent_win.cli import _split_global_options, _split_mode_option, run  # noqa: E402
from code_agent_win.rewind_sessions import CoordinatedSessionRepository  # noqa: E402
from code_agent_win.rewind_runtime import RewindRuntime  # noqa: E402
from tests.test_agent_app_full_stack import FakeModel  # noqa: E402


def _configured_application(container: Path):
    root, product = container / "workspace", container / "state"
    root.mkdir()
    runtime = load_runtime_config(env={
        "CHAOS_CONFIG": str(container / "missing.toml"),
        "CHAOS_API": "responses",
        "CHAOS_BASE_URL": "https://api.example.test",
        "CHAOS_MODEL": "test", "CHAOS_API_KEY_ENV": "KEY",
        "CHAOS_APPROVAL_MODE": "full-local",
    })
    patches = (
        patch.dict("os.environ", {
            "USERPROFILE": str(container / "profile"),
            "LOCALAPPDATA": str(container / "localappdata"),
        }, clear=True),
        patch("code_agent_win.app._model_client", return_value=object()),
        patch("code_agent_win.app._session_path",
              return_value=product / "sessions.sqlite3"),
        patch("code_agent_win.app._product_state_root", return_value=product),
        patch("code_agent_win.app.load_runtime_config", return_value=runtime),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        return create_application(root), root, product


class ApplicationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_aclose_closes_each_resource_once_in_order(self) -> None:
        calls: list[str] = []

        class AsyncCloser:
            def __init__(self, name: str) -> None:
                self.name = name

            async def aclose(self) -> None:
                calls.append(self.name)

        subagents = AsyncCloser("subagents")
        model = AsyncCloser("model")
        mcp = AsyncCloser("mcp")
        application = Application(
            controller=object(), foreground_tasks=object(), tui=object(),
            dispatcher=object(), model=model, mcp=mcp, subagents=subagents,
        )

        await application.aclose()

        self.assertEqual(calls, ["subagents", "model", "mcp"])
        self.assertEqual({name: calls.count(name) for name in calls}, {
            "subagents": 1, "model": 1, "mcp": 1,
        })


class CliFailureTests(unittest.IsolatedAsyncioTestCase):
    def test_global_options_are_removed_before_command_parsing(self) -> None:
        profile, model, command = _split_global_options(
            ("--profile", "company", "ask", "inspect", "--model", "fast")
        )

        self.assertEqual(
            (profile, model, command),
            ("company", "fast", ("ask", "inspect")),
        )
        with self.assertRaises(ValueError):
            _split_global_options(
                ("--model", "fast", "--model", "slow", "ask", "inspect")
            )

        mode, remaining = _split_mode_option(
            ("ask", "inspect", "--mode", "ultra")
        )
        self.assertEqual((mode, remaining), ("ultra", ("ask", "inspect")))
        with self.assertRaises(ValueError):
            _split_mode_option(("--mode", "unbounded", "ask", "inspect"))

    async def test_cli_reports_safe_error_without_a_traceback(self) -> None:
        stderr = StringIO()

        with patch(
            "code_agent_win.cli.create_application",
            side_effect=RuntimeError("secret detail"),
        ):
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

    def test_application_uses_coordinated_sessions_for_engine_and_foreground(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            application, _, _ = _configured_application(Path(temporary).resolve())
        sessions = application.tui.sessions
        self.assertIsInstance(sessions, CoordinatedSessionRepository)
        self.assertIs(application.foreground_tasks._sessions, sessions)
        self.assertIs(application.controller._engine._journal._repository, sessions)
        self.assertIs(application.tui.history, sessions)
        self.assertIs(application.tui.evidence, sessions)


class ManagedWorkspaceApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_task_uses_managed_worktree_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            source_before = _tree_digest(root)
            application = _configured_application(root)

            task = await application.foreground_tasks.start("edit note.py")
            task_root = Path(task.contract.authorization.workspace_root)

            self.assertNotEqual(task_root, root)
            self.assertIn("managed-workspaces", str(task_root))
            self.assertEqual(_tree_digest(root), source_before)
            self.assertEqual(application.workspace_root_for(task.id), task_root)
            self.assertEqual(application.runtime_root_for(task.id), task_root)
            self.assertEqual(application.verification_root_for(task.id), task_root)
            await application.aclose()

    async def test_non_git_source_stays_available_without_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "note.py").write_text("print('hi')\n", encoding="utf-8")
            application = _configured_application(root)

            task = await application.foreground_tasks.start("inspect")

            self.assertEqual(
                Path(task.contract.authorization.workspace_root), root
            )
            with self.assertRaises(RuntimeError):
                await application.tui.checkpoints.list(task.id)
            await application.aclose()

    async def test_managed_worktree_failure_does_not_fallback_to_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)

            with patch.object(
                application.workspace_runtime,
                "prepare_task",
                side_effect=WorkspaceError("cannot create worktree"),
            ):
                with self.assertRaises(RuntimeError):
                    await application.foreground_tasks.start("edit safely")

            self.assertEqual(await application.foreground_tasks._sessions.list_tasks(), ())
            await application.aclose()

    async def test_active_managed_task_blocks_second_task_from_same_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)

            await application.foreground_tasks.start("first")

            with self.assertRaises(RuntimeError):
                await application.foreground_tasks.start("second")
            await application.aclose()

    async def test_startup_hydrates_persisted_worktree_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            first = _configured_application(root)
            task = await first.foreground_tasks.start("edit note.py")
            task_root = Path(task.contract.authorization.workspace_root)
            await first.aclose()

            restarted = _configured_application(root)
            await restarted.startup()

            self.assertEqual(restarted.workspace_root_for(task.id), task_root)
            self.assertEqual(
                restarted.workspace_runtime.root_for_thread(task.thread_id),
                task_root,
            )
            await restarted.aclose()

    async def test_session_rewind_binds_replacement_task_to_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)
            task = await application.foreground_tasks.start("rewind session")
            task_root = Path(task.contract.authorization.workspace_root)
            await application.tui.sessions.get_or_create_task_budget(
                task.thread_id, "test", EngineLimits()
            )
            checkpoint = await application.tui.checkpoints.create(
                task.id, "rewindable"
            )
            preview = await application.tui.checkpoints.preview_rewind(
                task.id, checkpoint.id, "session"
            )
            await application.tui.sessions.transition_task(
                task.id, TaskStatus.PAUSED, "test quiesce"
            )

            result = await application.tui.checkpoints.execute_rewind(
                preview, confirmed=True
            )

            self.assertIsNotNone(result.replacement_task_id)
            self.assertEqual(
                application.workspace_root_for(result.replacement_task_id),
                task_root,
            )
            replacement = await application.tui.sessions.load_task(
                result.replacement_task_id
            )
            self.assertEqual(
                application.workspace_runtime.root_for_thread(
                    replacement.thread_id
                ),
                task_root,
            )
            await application.aclose()


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
            application.dispatcher.capture = None
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
                execution_context=ActionExecutionContext(
                    "thread-index",
                    "thread-index",
                    "write-indexed-file",
                ),
            )
            after = application.repo_index.snapshot_for_turn()

            self.assertFalse(result.is_error, result.output)
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

    def test_application_injects_one_shared_rewind_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            application, _, _ = _configured_application(Path(temporary).resolve())
        self.assertIsInstance(application.rewind, RewindRuntime)
        self.assertIs(application.tui.rewind, application.rewind)

    def test_application_uses_rewind_session_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            application, _, _ = _configured_application(Path(temporary).resolve())
        capture = application.dispatcher.capture
        self.assertIsInstance(capture.sessions, RewindSessionRepository)
        self.assertIs(application.tui.sessions._base, capture.sessions)
        self.assertIs(application.rewind.sessions, capture.sessions)
        self.assertIs(application.rewind.snapshots, capture.snapshots)
        self.assertIs(application.rewind.editor, capture.editor)
        self.assertIs(application.tui.sessions._gate, capture.gate)

    def test_snapshot_product_state_is_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            application, root, product = _configured_application(Path(temporary).resolve())
        self.assertFalse(product.is_relative_to(root))
        self.assertEqual(
            application.dispatcher.capture.snapshots._artifacts.root,
            product / "rewind-snapshots",
        )

    def test_product_state_paths_match_snapshot_and_gate_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            application, _, product = _configured_application(Path(temporary).resolve())
        capture = application.dispatcher.capture
        fingerprint = capture.snapshots.workspace_fingerprint
        self.assertEqual(capture.snapshots._artifacts.root, product / "rewind-snapshots")
        self.assertEqual(
            capture.gate.path,
            product / "rewind" / fingerprint / "mutation-gate.sqlite3",
        )
        self.assertEqual(
            capture.sessions._database.path, product / "sessions.sqlite3"
        )
        self.assertIs(capture.existing_baseline, RewindBaseline.NON_GIT_EXISTING)

    def test_application_uses_keyword_construction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "code_agent_win.app.Application"
        ) as application_type:
            _configured_application(Path(temporary).resolve())
        args, kwargs = application_type.call_args
        self.assertEqual(args, ())
        self.assertIn("rewind", kwargs)


class ApplicationGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_production_guard_rejects_external_path_before_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            application, root, _ = _configured_application(Path(temporary).resolve())
            outside = root.parent / "outside.txt"
            capture = application.dispatcher.capture
            with patch.object(
                capture, "apply_edit", new=AsyncMock()
            ) as apply_edit:
                result = await application.dispatcher.dispatch(
                    ActionRequest(
                        "external", "write_file",
                        {"path": str(outside), "content": "forbidden"},
                    ),
                    CancellationToken(),
                )
            self.assertTrue(result.is_error)
            self.assertFalse(outside.exists())
            apply_edit.assert_not_awaited()

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
                current = _session_path()

            self.assertEqual(current, root / "chaos-agent" / "sessions.sqlite3")

    def test_product_state_root_uses_the_chaos_agent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            with patch("code_agent_win.app.os.getenv", return_value=str(root)):
                current = _product_state_root()

            self.assertEqual(current, root / "chaos-agent")

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


def _configured_application(root: Path):
    if not (root / ".git").exists() and not (root / "note.py").exists():
        workspace, product = root / "workspace", root / "state"
        workspace.mkdir()
        runtime = load_runtime_config(env={
            "CHAOS_CONFIG": str(root / "missing.toml"),
            "CHAOS_API": "responses",
            "CHAOS_BASE_URL": "https://api.example.test",
            "CHAOS_MODEL": "test",
            "CHAOS_API_KEY_ENV": "KEY",
            "CHAOS_APPROVAL_MODE": "full-local",
        })
        patches = (
            patch.dict("os.environ", {
                "USERPROFILE": str(root / "profile"),
                "LOCALAPPDATA": str(root / "localappdata"),
            }, clear=True),
            patch("code_agent_win.app._model_client", return_value=object()),
            patch("code_agent_win.app._session_path",
                  return_value=product / "sessions.sqlite3"),
            patch("code_agent_win.app._product_state_root", return_value=product),
            patch("code_agent_win.app.load_runtime_config", return_value=runtime),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            return create_application(workspace), workspace, product
    state = root.parent / f"{root.name}-state"
    state.mkdir(exist_ok=True)
    runtime = load_runtime_config(env={
        "CHAOS_CONFIG": str(root / "missing.toml"),
        "CHAOS_API": "responses",
        "CHAOS_BASE_URL": "https://api.example.test",
        "CHAOS_MODEL": "test",
        "CHAOS_API_KEY_ENV": "KEY",
        "CHAOS_APPROVAL_MODE": "auto",
    })
    mode_env = {
        f"CHAOS_MODE_{mode.value.upper()}_PROFILE": runtime.profile
        for mode in agent_modes.AgentMode
    }
    patches = (
        patch("code_agent_win.app._model_client", side_effect=lambda _: object()),
        patch("code_agent_win.app._session_path", return_value=state / "sessions.sqlite3"),
        patch("code_agent_win.app.load_runtime_config", return_value=runtime),
        patch.dict("os.environ", mode_env),
    )
    stack = contextlib.ExitStack()
    for item in patches:
        stack.enter_context(item)
    application = create_application(root)
    application._test_stack = stack
    return application


def _init_git_source(root: Path) -> None:
    (root / "note.py").write_text("print('source')\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "note.py")
    _git(root, "commit", "-m", "initial")
    (root / "note.py").write_text("print('dirty')\n", encoding="utf-8")


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".git" not in path.parts:
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
