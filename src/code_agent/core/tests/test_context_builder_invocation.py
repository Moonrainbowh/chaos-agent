from __future__ import annotations

import unittest

from code_agent.core.context_request import ContextRequest
from code_agent.core.engine import AgentEngine
from code_agent.core.errors import ContextBuildError
from code_agent.core.events import EventKind
from code_agent.core.tests._engine_support import (
    FakeActionDispatcher,
    FakeModelClient,
    MemorySessionRepository,
)


class ContextBuilderInvocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_internal_type_error_is_not_retried(self) -> None:
        class FailingContext:
            def __init__(self) -> None:
                self.calls = 0

            async def build(self, request: ContextRequest) -> object:
                self.calls += 1
                raise TypeError("private builder detail")

        class FailingLegacyContext:
            def __init__(self) -> None:
                self.calls = 0

            async def build(
                self,
                thread_id: object,
                messages: object,
                user_input: object,
                tools: object,
                task_state: object,
                cancellation: object,
            ) -> object:
                self.calls += 1
                raise TypeError("private legacy builder detail")

        for context in (FailingContext(), FailingLegacyContext()):
            with self.subTest(context=type(context).__name__):
                sessions = MemorySessionRepository()
                engine = AgentEngine(
                    FakeModelClient(()),
                    context,  # type: ignore[arg-type]
                    FakeActionDispatcher(),
                    sessions,
                )

                with self.assertRaisesRegex(
                    ContextBuildError, "context build failed"
                ):
                    _ = [event async for event in engine.run("inspect")]

                self.assertEqual(context.calls, 1)
                self.assertEqual(
                    sessions.events["thread-1"][-1].kind, EventKind.ERROR
                )


if __name__ == "__main__":
    unittest.main()
