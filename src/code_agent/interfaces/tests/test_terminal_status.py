from __future__ import annotations

import unittest

from code_agent.interfaces.i18n import Language
from code_agent.interfaces.terminal_status import status_context, status_presentation
from code_agent.interfaces.terminal_theme import Theme


class TerminalStatusTests(unittest.TestCase):
    def test_status_context_includes_nonzero_phase_breakdown(self) -> None:
        result = status_context(
            "model", 0, 5, phase_durations={"context": 125, "model": 2_000, "action": 60_500}
        )

        self.assertEqual(result, "model · ctx 0.1s model 2.0s tools 60.5s · 00:05")

    def test_paused_status_includes_the_durable_stop_reason(self) -> None:
        label, icon, _ = status_presentation(
            "paused",
            "",
            None,
            Language.EN_US,
            Theme.MODERN,
            0,
            "active time budget exceeded",
        )

        self.assertEqual(icon, "!")
        self.assertEqual(label, "paused · active time budget exceeded")
