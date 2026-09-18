from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.events import AgentEvent, EventKind  # noqa: E402
from code_agent.core.attachments import AttachmentRef  # noqa: E402
from code_agent.core.models import ModelEvent, ModelEventKind  # noqa: E402
from code_agent.interfaces.commands import (  # noqa: E402
    CommandKind,
    execute_command,
    parse_command,
)
from code_agent.interfaces.controller import AgentController  # noqa: E402
from code_agent.interfaces.tests._support import FakeEngine  # noqa: E402


class _Tasks:
    def __init__(self, events: tuple[AgentEvent, ...]) -> None:
        self._events = events
        self.started: list[str] = []
        self.event_calls: list[tuple[str, str, tuple[AttachmentRef, ...]]] = []

    async def start(self, prompt: str):
        self.started.append(prompt)
        return type("Task", (), {"id": "task-1"})()

    async def events(
        self, task_id: str, prompt: str, *, attachments: tuple[AttachmentRef, ...]
    ):
        self.event_calls.append((task_id, prompt, attachments))
        for event in self._events:
            yield event


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
        tasks = _Tasks((event, AgentEvent(EventKind.COMPLETED)))

        status = await execute_command(
            parse_command(("ask", "question")),
            AgentController(FakeEngine(())),
            object(),  # type: ignore[arg-type]
            output.append,
            tasks,
        )

        self.assertEqual(status, 0)
        self.assertEqual(tasks.started, ["question"])
        self.assertEqual(tasks.event_calls[0][:2], ("task-1", "question"))
        rendered = "".join(output)
        self.assertIn("Result", rendered)
        self.assertIn("answer", rendered)
        self.assertNotIn("###", rendered)
        self.assertNotIn("**", rendered)

    async def test_ask_forwards_cli_attachment_references(self) -> None:
        attachment = AttachmentRef("a" * 64, "text/plain", 4, "note.txt")
        tasks = _Tasks(())

        status = await execute_command(
            parse_command(("ask", "inspect")),
            AgentController(FakeEngine(())),
            object(),  # type: ignore[arg-type]
            lambda _: None,
            tasks,
            attachments=(attachment,),
        )

        self.assertEqual(status, 0)
        self.assertEqual(tasks.event_calls[0][2], (attachment,))

    async def test_json_run_uses_the_same_durable_task_event_stream(self) -> None:
        event = AgentEvent(EventKind.TASK_STATUS_CHANGED, {"task_id": "task-1", "status": "running"})
        tasks = _Tasks((event,))
        output: list[str] = []

        status = await execute_command(
            parse_command(("run", "--json", "modify", "note")),
            AgentController(FakeEngine(())),
            object(),  # type: ignore[arg-type]
            output.append,
            tasks,
        )

        self.assertEqual(status, 0)
        self.assertEqual(tasks.started, ["modify note"])
        self.assertIn('"task_id":"task-1"', output[0])


if __name__ == "__main__":
    unittest.main()
