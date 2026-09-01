from __future__ import annotations

import unittest
from types import SimpleNamespace

from code_agent.interfaces.tui_command_dispatch import handle_tui_command
from code_agent.interfaces.tui_commands import parse_tui_command


_HOST_RUNTIME = (
    "powershell_7 · PowerShell 7.6.5; "
    "paths=legacy-safe<=240 UTF-16 units (LongPathsEnabled=0)"
)


class _RuntimeSelection:
    def __init__(self) -> None:
        self.current = _selection()

    def profiles(self):
        return (SimpleNamespace(name="sol", model="gpt-sol"),)

    async def use(self, **values):
        del values
        return self.current


class _App:
    def __init__(self) -> None:
        self.active_task_id = None
        self.current_thread_id = None
        self.state = SimpleNamespace(status="idle", task_id=None)
        self.host_runtime_summary = _HOST_RUNTIME
        self.runtime_selection = _RuntimeSelection()
        self.modes = None
        self._run_task = None
        self.messages: list[str] = []

    def _current_model(self) -> str:
        return "gpt-sol"

    def _append(self, kind: object, message: str) -> None:
        del kind
        self.messages.append(message)


class RuntimeStatusSurfaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_includes_host_runtime_and_path_policy(self) -> None:
        app = _App()

        await handle_tui_command(app, parse_tui_command("/状态"))

        self.assertIn("powershell_7", app.messages[-1])
        self.assertIn("paths=legacy-safe", app.messages[-1])

    async def test_runtime_switch_result_keeps_host_path_policy_visible(self) -> None:
        app = _App()

        await handle_tui_command(
            app,
            parse_tui_command("/模式 思考 high", {"runtime_selection"}),
        )

        self.assertIn("runtime selected", app.messages[-1])
        self.assertIn("powershell_7", app.messages[-1])
        self.assertIn("paths=legacy-safe", app.messages[-1])


def _selection():
    return SimpleNamespace(
        topology="single",
        profile="sol",
        model="gpt-sol",
        reasoning_effort="high",
    )


if __name__ == "__main__":
    unittest.main()
