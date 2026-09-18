from __future__ import annotations

import unittest
from unittest.mock import patch

from code_agent.interfaces.terminal_io import read_key
from code_agent.interfaces.posix_terminal_io import read_key as read_posix_key


class TerminalPasteInputTests(unittest.TestCase):
    def test_empty_bracketed_paste_is_preserved_as_an_input_event(self) -> None:
        class ConsoleInput:
            def __init__(self) -> None:
                self.characters = list("\x1b[200~\x1b[201~")

            def kbhit(self) -> bool:
                return bool(self.characters)

            def getwch(self) -> str:
                return self.characters.pop(0)

        with patch("code_agent.interfaces.terminal_io.os.name", "nt"), patch.dict(
            "sys.modules", {"msvcrt": ConsoleInput()}
        ):
            self.assertEqual(read_key(), "\x1b[200~\x1b[201~")

    def test_shift_carriage_return_is_normalized_as_shift_enter(self) -> None:
        class ConsoleInput:
            def kbhit(self) -> bool:
                return False

            def getwch(self) -> str:
                return "\r"

        with patch("code_agent.interfaces.terminal_io.os.name", "nt"), patch.dict(
            "sys.modules", {"msvcrt": ConsoleInput()}
        ), patch(
            "code_agent.interfaces.terminal_io._shift_is_pressed", return_value=True
        ):
            self.assertEqual(read_key(), "shift+enter")

    def test_timed_read_returns_without_starting_a_blocking_executor_wait(self) -> None:
        class ConsoleInput:
            def kbhit(self) -> bool:
                return False

        with patch.dict("sys.modules", {"msvcrt": ConsoleInput()}):
            self.assertIsNone(read_key(timeout=0))

    def test_posix_escape_sequence_is_decoded_without_windows_console_apis(self) -> None:
        with patch(
            "code_agent.interfaces.posix_terminal_io._read_character",
            side_effect=("\x1b", "[", "A"),
        ):
            self.assertEqual(read_posix_key(), "up")

    def test_posix_escape_preserves_the_following_printable_character(self) -> None:
        with patch(
            "code_agent.interfaces.posix_terminal_io._read_character",
            side_effect=("\x1b", "x"),
        ):
            self.assertEqual(read_posix_key(), "\x1b")
            self.assertEqual(read_posix_key(), "x")


if __name__ == "__main__":
    unittest.main()
