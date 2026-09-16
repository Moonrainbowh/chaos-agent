from __future__ import annotations

import unittest

from code_agent.core.cancellation import CancellationToken
from code_agent.core.engine import AgentEngine
from code_agent.core.events import EventKind
from code_agent.core.models import (
    ActionResult,
    ModelEvent,
    ModelEventKind,
    ToolCall,
)
from code_agent.core.tests._engine_support import (
    FakeActionDispatcher,
    FakeContextBuilder,
    FakeModelClient,
    MemorySessionRepository,
)


class EngineActionCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_token_stops_the_run_after_recording_the_action(
        self,
    ) -> None:
        token = CancellationToken()

        class CancellingDispatcher(FakeActionDispatcher):
            async def dispatch(
                self,
                request,
                cancellation,
                task_authorization=None,
                *,
                execution_context=None,
            ):
                self.requests.append(request)
                token.cancel("user stop")
                return ActionResult(request.id, request.name, {"text": "contents"})

        actions = CancellingDispatcher()
        model = FakeModelClient(
            (
                (
                    ModelEvent(
                        ModelEventKind.TOOL_CALL,
                        tool_call=ToolCall("call-1", "read_file", {"path": "a.txt"}),
                    ),
                    ModelEvent(
                        ModelEventKind.TOOL_CALL,
                        tool_call=ToolCall("call-2", "read_file", {"path": "b.txt"}),
                    ),
                    ModelEvent(ModelEventKind.COMPLETED),
                ),
            )
        )
        sessions = MemorySessionRepository()

        events = [
            event
            async for event in AgentEngine(
                model, FakeContextBuilder(), actions, sessions
            ).run("inspect", cancellation=token)
        ]

        kinds = [event.kind for event in events]
        self.assertEqual(kinds[-1], EventKind.CANCELLED)
        # The action that observed the cancellation is still durable before the
        # run stops: the verification ledger expires evidence from the recorded
        # result, so dropping it would leave pre-cancellation evidence valid.
        self.assertIn(EventKind.ACTION_COMPLETED, kinds)
        self.assertIn(EventKind.MESSAGE_ADDED, kinds)
        self.assertLess(kinds.index(EventKind.MESSAGE_ADDED), kinds.index(EventKind.CANCELLED))
        # The sibling call in the same turn never runs.
        self.assertEqual([item.name for item in actions.requests], ["read_file"])
        self.assertEqual(len(model.calls), 1)


if __name__ == "__main__":
    unittest.main()
