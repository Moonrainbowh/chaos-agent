from __future__ import annotations

import unittest
from types import SimpleNamespace

from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.task_mode_control import TaskModeControl
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp


class Runtime:
    def __init__(self):
        self.current = SimpleNamespace(profile="gpt56_sol", model="Sol", topology="single", reasoning_effort="medium")
        self.calls = []

    def profiles(self):
        return (("gpt56_sol", "Sol", "chat_completions"), ("gpt56_terra", "Terra", "chat_completions"))

    async def use(self, *, idle, **values):
        if not idle:
            raise RuntimeError("busy")
        self.calls.append(values)
        for key, value in values.items():
            setattr(self.current, key, value)
        return self.current


def make_app(**controls):
    app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None, **controls)
    app.play_sound = lambda **_: None
    return app


class CommandNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_and_effort_enter_real_choices_and_apply(self):
        runtime = Runtime()
        app = make_app(runtime_selection=runtime)
        for command, expected in (("/model", "/model "), (":effort", ":effort ")):
            app.input.replace(command)
            await app.handle_key("\r")
            self.assertEqual(app.input.text, expected)
            self.assertIn("Current", "\n".join(app.interactions.rows(app)))
            await app.handle_key("down")
            await app.handle_key("\r")
            self.assertEqual(app.input.text, "")
        self.assertEqual(runtime.current.profile, "gpt56_terra")
        self.assertEqual(runtime.current.reasoning_effort, "low")
        self.assertEqual(len(runtime.calls), 2)

    async def test_tab_completes_and_escape_returns_one_level_without_applying(self):
        runtime = Runtime()
        app = make_app(runtime_selection=runtime)
        app.input.replace("/model terr")
        await app.handle_key("\t")
        self.assertEqual(app.input.text, "/model gpt56_terra")
        for expected in ("/model ", "/", ""):
            await app.handle_key("\x1b")
            self.assertEqual(app.input.text, expected)
        self.assertEqual(runtime.calls, [])

    async def test_missing_argument_keeps_usage_and_valid_input_executes(self):
        app = make_app()
        app.input.replace("/map context ")
        await app.handle_key("\r")
        self.assertEqual(app.input.text, "/map context ")
        self.assertIn("<query>", "\n".join(app.interactions.rows(app)))
        app.input.replace("/status unexpected")
        await app.handle_key("\r")
        self.assertEqual(app.input.text, "/status unexpected")
        self.assertIn("does not accept arguments", app.state.entries[-1].text)

    async def test_unknown_choice_cannot_fall_through_to_another_command(self):
        runtime = Runtime()
        app = make_app(runtime_selection=runtime)
        app.input.replace("/effort banana")
        await app.handle_key("\r")
        self.assertEqual(app.input.text, "/effort banana")
        self.assertEqual(runtime.calls, [])
        self.assertIn("No matching", "\n".join(app.interactions.rows(app)))

    async def test_command_failure_keeps_input_and_allows_the_next_command(self):
        class Tasks:
            async def accept_partial(self, *args):
                raise RuntimeError("Only waiting tasks can be accepted partially.")

        app = make_app(tasks=Tasks())
        app.active_task_id = "paused-task"
        app.input.replace("/accept")
        await app.handle_key("\r")
        self.assertEqual(app.input.text, "/accept")
        self.assertEqual(app.active_task_id, "paused-task")
        self.assertIn("Only waiting tasks", app.state.entries[-1].text)
        app.input.replace("/help")
        await app.handle_key("\r")
        self.assertEqual(app.input.text, "")

    async def test_saved_task_settings_cannot_be_silently_replaced(self):
        runtime = Runtime()
        app = make_app(runtime_selection=runtime, task_modes=TaskModeControl())
        app.active_task_id = "waiting-task"
        app.state.status = "waiting_decision"
        for command in ("/model terra", "/effort high", "/mode ask"):
            self.assertFalse(await app.submit(command))
            self.assertIn("/new", app.state.entries[-1].text)
        self.assertEqual(runtime.calls, [])
        self.assertEqual(app.task_modes.current.name, "code")

    async def test_advanced_commands_are_searchable_but_do_not_expand_root(self):
        app = make_app()
        app.input.replace("/")
        app.interactions.rows(app)
        self.assertNotIn("/help", [item.label for item in app.interactions.picker.matches])
        app.input.replace("/help")
        app.interactions.rows(app)
        self.assertEqual(app.interactions.picker.selected.label, "/help")

    async def test_history_opens_a_searchable_session_picker_and_restores_selection(self):
        class Sessions:
            async def list_threads(self):
                return (SimpleNamespace(id="thread-old", title="Previous investigation"),)

        app = make_app(sessions=Sessions(), history=object())
        restored = []

        async def restore(identifier):
            restored.append(identifier)
            return True

        app.restore_thread = restore
        await app.submit("/sessions history")
        self.assertEqual(app.input.text, "/restore ")
        self.assertIn("Previous investigation", "\n".join(app.interactions.rows(app)))
        await app.handle_key("\r")
        self.assertEqual(restored, ["thread-old"])

    async def test_attachment_menu_and_missing_id_do_not_execute_an_operation(self):
        app = make_app(attachment_draft=SimpleNamespace(items=()))
        app.input.replace("/attach")
        await app.handle_key("\r")
        app.interactions.rows(app)
        self.assertEqual([item.label for item in app.interactions.picker.matches], ["/attach add", "/attach clipboard", "/attach list", "/attach remove", "/attach clear"])
        app.input.replace("/attach remove ")
        await app.handle_key("\r")
        self.assertEqual(app.input.text, "/attach remove ")
        self.assertEqual(app.state.entries, [])
