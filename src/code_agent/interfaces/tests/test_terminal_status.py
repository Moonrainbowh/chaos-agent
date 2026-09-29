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

    def test_status_context_shows_completion_duration_and_relative_age(self) -> None:
        result = status_context(
            "model", None, 610, completed_at=0, completed_duration_ms=83_000
        )

        self.assertIn("耗时 1分23秒", result)
        self.assertIn("10分钟前", result)

    def test_status_context_uses_just_now_for_recent_completion(self) -> None:
        result = status_context(
            "model", None, 5, completed_at=0, completed_duration_ms=900
        )

        self.assertIn("耗时 1秒", result)
        self.assertIn("刚刚", result)

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
