from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from code_agent.interfaces.terminal_size import terminal_size, viewport_at_bottom


class TerminalSizeTests(unittest.TestCase):
    def test_non_windows_fallback_is_bounded(self) -> None:
        with patch("code_agent.interfaces.terminal_size.os.name", "posix"), patch(
            "code_agent.interfaces.terminal_size.shutil.get_terminal_size",
            return_value=os.terminal_size((80, 24)),
        ):
            self.assertEqual(terminal_size(), (80, 24))

    def test_non_windows_viewport_is_treated_as_at_bottom(self) -> None:
        with patch("code_agent.interfaces.terminal_size.os.name", "posix"):
            self.assertTrue(viewport_at_bottom())
