from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.events import AgentEvent, EventKind  # noqa: E402
from code_agent.core.models import ModelEvent, ModelEventKind  # noqa: E402
from code_agent.interfaces.commands import (  # noqa: E402
    CommandKind,
    execute_command,
    parse_command,
)
from code_agent.interfaces.controller import AgentController  # noqa: E402
from code_agent.interfaces.tests._support import FakeEngine  # noqa: E402


class CommandParsingTests(unittest.TestCase):
    def test_default_command_opens_tui(self) -> None:
        command = parse_command(())

        self.assertEqual(command.kind, CommandKind.TUI)
        self.assertIsNone(command.thread_id)

    def test_ask_and_json_run_keep_prompt_as_one_value(self) -> None:
        ask = parse_command(("ask", "explain", "this"))
        run = parse_command(("run", "--json", "inspect", "src"))

        self.assertEqual(ask.kind, CommandKind.ASK)
        self.assertEqual(ask.prompt, "explain this")
        self.assertEqual(run.kind, CommandKind.RUN_JSON)
        self.assertEqual(run.prompt, "inspect src")

    def test_resume_opens_tui_without_prompt_or_runs_request_with_prompt(self) -> None:
        tui = parse_command(("resume", "thread-1"))
        ask = parse_command(("resume", "thread-1", "continue"))

        self.assertEqual(tui.kind, CommandKind.TUI)
        self.assertEqual(tui.thread_id, "thread-1")
        self.assertEqual(ask.kind, CommandKind.RESUME)
        self.assertEqual(ask.thread_id, "thread-1")
        self.assertEqual(ask.prompt, "continue")

    def test_invalid_commands_raise_usage_error_instead_of_exiting(self) -> None:
        for arguments in (("ask",), ("run", "text"), ("unknown",)):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    parse_command(arguments)


class CommandExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_ask_renders_markdown_instead_of_printing_source_markers(self) -> None:
        event = AgentEvent(
            EventKind.MODEL_EVENT,
            {"event": ModelEvent(ModelEventKind.TEXT_DELTA, text="### Result\n\n**answer**").to_dict()},
        )
        output: list[str] = []

        status = await execute_command(
            parse_command(("ask", "question")),
            AgentController(FakeEngine((event, AgentEvent(EventKind.COMPLETED)))),
            object(),  # type: ignore[arg-type]
            output.append,
        )

        self.assertEqual(status, 0)
        rendered = "".join(output)
        self.assertIn("Result", rendered)
        self.assertIn("answer", rendered)
        self.assertNotIn("###", rendered)
        self.assertNotIn("**", rendered)


if __name__ == "__main__":
    unittest.main()
