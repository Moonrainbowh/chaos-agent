from __future__ import annotations

import asyncio
import re
import sys
import unittest
from collections.abc import AsyncIterator
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import Message, ModelEvent, ModelEventKind
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.windows_tui import WindowsTerminalApp


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _plain(value: str) -> str:
    return _ANSI.sub("", value)


class StreamingTuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_streamed_answer_is_visible_before_completion_and_finalized_once(self) -> None:
        release = asyncio.Event()

        class GatedEngine:
            async def run(self, *_: object, **__: object) -> AsyncIterator[AgentEvent]:
                yield _text_delta("live text")
                await release.wait()
                yield AgentEvent(
                    EventKind.MESSAGE_ADDED,
                    {"message": Message(role="assistant", content="live text").to_dict()},
                )
                yield AgentEvent(EventKind.COMPLETED, {})

        output: list[str] = []
        draft_visible = asyncio.Event()

        def write(value: str) -> None:
            output.append(value)
            if "live text" in _plain(value):
                draft_visible.set()

        app = WindowsTerminalApp(
            AgentController(GatedEngine()), ApprovalBroker(), write=write
        )

        await app.submit("inspect")
        await asyncio.wait_for(draft_visible.wait(), timeout=1)
        self.assertIn("live text", _plain("".join(output)))
        release.set()
        await app.wait_idle()
        self.assertEqual(_plain("".join(output)).count("✦ Chaos Agent\n  live text"), 1)

    async def test_cancelled_draft_is_rendered_once_without_entering_transcript(self) -> None:
        release = asyncio.Event()

        class GatedEngine:
            async def run(self, *_: object, **__: object) -> AsyncIterator[AgentEvent]:
                yield _text_delta("unfinished")
                await release.wait()
                yield AgentEvent(EventKind.CANCELLED, {"reason": "user requested pause"})

        output: list[str] = []
        draft_visible = asyncio.Event()

        def write(value: str) -> None:
            output.append(value)
            if "unfinished" in _plain(value):
                draft_visible.set()

        app = WindowsTerminalApp(
            AgentController(GatedEngine()), ApprovalBroker(), write=write
        )

        await app.submit("inspect")
        await asyncio.wait_for(draft_visible.wait(), timeout=1)
        release.set()
        await app.wait_idle()

        plain = _plain("".join(output))
        self.assertEqual(plain.count("! Incomplete response"), 1)
        self.assertNotIn("unfinished", app.state.transcript)


def _text_delta(text: str) -> AgentEvent:
    return AgentEvent(
        EventKind.MODEL_EVENT,
        {"event": ModelEvent(ModelEventKind.TEXT_DELTA, text=text).to_dict()},
    )


if __name__ == "__main__":
    unittest.main()
