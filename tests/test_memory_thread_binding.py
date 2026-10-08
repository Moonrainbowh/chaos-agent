import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from chaos_agent.user_command_control import UserCommandControl
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionResult


class UserCommandThreadBindingTests(unittest.IsolatedAsyncioTestCase):
    def control(self, created):
        sessions = SimpleNamespace(create_thread=AsyncMock(return_value="new-thread"),
            append_message=AsyncMock(), append_event=AsyncMock())
        async def dispatch(request, cancellation, authorization, *, execution_context):
            self.assertEqual(execution_context.owner_thread_id, created if created else "existing")
            return ActionResult(request.id, request.name, {"returncode": 0, "stdout": "ok"}, False)
        dispatcher = SimpleNamespace(dispatch=dispatch,
            editor=SimpleNamespace(guard=SimpleNamespace(root=Path.cwd())))
        binding = Mock()
        return UserCommandControl(sessions, dispatcher, on_thread_created=binding), sessions, binding

    async def test_fresh_command_thread_is_bound_before_any_journal_or_execution(self):
        control, sessions, binding = self.control("new-thread")
        async def append(thread_id, message):
            binding.assert_called_once_with(thread_id)
        sessions.append_message.side_effect = append
        thread_id, result = await control.run("echo ok", None, CancellationToken())
        self.assertEqual(thread_id, "new-thread")
        self.assertFalse(result.is_error)
        binding.assert_called_once_with("new-thread")
        sessions.create_thread.assert_awaited_once_with()
        self.assertEqual(sessions.append_event.await_count, 2)

    async def test_existing_or_failed_creation_never_registers_thread(self):
        control, sessions, binding = self.control(None)
        await control.run("echo ok", "existing", CancellationToken(), include=False)
        sessions.create_thread.assert_not_awaited()
        binding.assert_not_called()
        sessions.create_thread.side_effect = RuntimeError("creation failed")
        with self.assertRaisesRegex(RuntimeError, "creation failed"):
            await control.run("echo ok", None, CancellationToken())
        binding.assert_not_called()
