from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.config.loader import load_runtime_config
from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest
from code_agent.sessions.rewind_models import RewindBaseline
from code_agent.sessions.rewind_repository import RewindSessionRepository
from code_agent_win import agent_modes
from code_agent_win.app import create_application
from code_agent_win.rewind_runtime import RewindRuntime
from tests.agent_app_test_support import _configured_application


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
        default_sessions = application.tui.sessions._coordinated(
            capture.editor.guard.root
        )
        self.assertIs(default_sessions._gate, capture.gate)

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


if __name__ == "__main__":
    unittest.main()
