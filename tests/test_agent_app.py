from __future__ import annotations

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
    def test_application_uses_coordinated_sessions_for_engine_and_foreground(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            application, _, _ = _configured_application(Path(temporary).resolve())
        sessions = application.tui.sessions
        self.assertIsInstance(sessions, CoordinatedSessionRepository)
        self.assertIs(application.foreground_tasks._sessions, sessions)
        self.assertIs(application.controller._engine._journal._repository, sessions)
        self.assertIs(application.tui.history, sessions)
        self.assertIs(application.tui.evidence, sessions)
        self.assertEqual(application.mode.definition.mode.value, "medium")
        self.assertEqual(application.mode.model, "test")
        self.assertIsNotNone(application.plugins)
        self.assertIsNotNone(application.subagents)

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
            "code_agent_win.app_factory.Application"
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


if __name__ == "__main__":
    unittest.main()
