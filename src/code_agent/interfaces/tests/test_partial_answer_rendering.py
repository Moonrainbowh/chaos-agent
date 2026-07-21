from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core.events import AgentEvent, EventKind  # noqa: E402
from code_agent.core.models import ModelEvent, ModelEventKind  # noqa: E402
from code_agent.interfaces.approval import ApprovalBroker  # noqa: E402
from code_agent.interfaces.controller import AgentController  # noqa: E402
from code_agent.interfaces.tests._support import FakeEngine  # noqa: E402
from code_agent.interfaces.windows_tui import WindowsTerminalApp  # noqa: E402


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _plain(value: str) -> str:
    return _ANSI.sub("", value)


class PartialAnswerRenderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_stream_flushes_a_static_partial_answer(self) -> None:
        events = (
            AgentEvent(EventKind.MODEL_EVENT, {"event": ModelEvent(ModelEventKind.TEXT_DELTA, text="unfinished").to_dict()}),
            AgentEvent(EventKind.CANCELLED, {"reason": "user requested pause"}),
        )
        output: list[str] = []
        app = WindowsTerminalApp(AgentController(FakeEngine(events)), ApprovalBroker(), write=output.append)

        try:
            await app._consume("inspect", CancellationToken())
        except KeyError as error:
            self.fail(f"partial answer did not render: {error}")

        self.assertIn("! unfinished", _plain("".join(output)))
        self.assertNotIn("unfinished", "\n".join(app.state.transcript))


if __name__ == "__main__":
    unittest.main()
