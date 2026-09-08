import unittest
from unittest.mock import Mock, patch

from code_agent_win import cli


class CliExitTests(unittest.TestCase):
    def test_keyboard_interrupt_exits_without_a_traceback(self) -> None:
        with patch("code_agent_win.cli.configure_windows_utf8_stdio"), patch(
            "code_agent_win.cli.run", new=Mock(return_value=object())
        ), patch("code_agent_win.cli.asyncio.run", side_effect=KeyboardInterrupt):
            self.assertEqual(cli.main(), 130)
