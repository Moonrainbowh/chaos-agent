from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.interfaces.token_rate import TokenRateTracker  # noqa: E402


class TokenRateTrackerTests(unittest.TestCase):
    def test_live_rate_uses_estimated_output_from_first_text_delta(self) -> None:
        now = 10.0
        tracker = TokenRateTracker(lambda: now)

        tracker.observe_text("abcdefgh")
        now = 12.0

        self.assertEqual(tracker.rate(), 1.0)

    def test_provider_usage_calibrates_and_freezes_completed_rate(self) -> None:
        now = 10.0
        tracker = TokenRateTracker(lambda: now)
        tracker.observe_text("estimated")
        now = 12.0
        tracker.calibrate(10)
        now = 20.0

        self.assertEqual(tracker.rate(), 5.0)

    def test_reset_and_usage_before_text_do_not_report_a_rate(self) -> None:
        now = 10.0
        tracker = TokenRateTracker(lambda: now)
        tracker.calibrate(8)
        self.assertIsNone(tracker.rate())

        tracker.observe_text("abcd")
        tracker.reset()
        now = 12.0
        self.assertIsNone(tracker.rate())


if __name__ == "__main__":
    unittest.main()
