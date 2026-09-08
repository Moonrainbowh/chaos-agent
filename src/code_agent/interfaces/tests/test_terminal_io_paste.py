from __future__ import annotations

import unittest
from unittest.mock import patch

from code_agent.interfaces.terminal_io import read_key


class TerminalPasteInputTests(unittest.TestCase):
    def test_empty_bracketed_paste_is_preserved_as_an_input_event(self) -> None:
        class ConsoleInput:
            def __init__(self) -> None:
                self.characters = list("\x1b[200~\x1b[201~")

            def kbhit(self) -> bool:
                return bool(self.characters)

            def getwch(self) -> str:
                return self.characters.pop(0)

        with patch.dict("sys.modules", {"msvcrt": ConsoleInput()}):
            self.assertEqual(read_key(), "\x1b[200~\x1b[201~")

    def test_shift_carriage_return_is_normalized_as_shift_enter(self) -> None:
        class ConsoleInput:
            def kbhit(self) -> bool:
                return False

            def getwch(self) -> str:
                return "\r"

        with patch.dict("sys.modules", {"msvcrt": ConsoleInput()}), patch(
            "code_agent.interfaces.terminal_io._shift_is_pressed", return_value=True
        ):
            self.assertEqual(read_key(), "shift+enter")

    def test_timed_read_returns_without_starting_a_blocking_executor_wait(self) -> None:
        class ConsoleInput:
            def kbhit(self) -> bool:
                return False

        with patch.dict("sys.modules", {"msvcrt": ConsoleInput()}):
            self.assertIsNone(read_key(timeout=0))


if __name__ == "__main__":
    unittest.main()
