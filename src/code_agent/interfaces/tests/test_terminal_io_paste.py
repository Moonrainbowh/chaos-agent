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


if __name__ == "__main__":
    unittest.main()
