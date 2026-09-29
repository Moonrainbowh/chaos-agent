import unittest
from unittest.mock import Mock, patch

from chaos_agent import cli


class CliExitTests(unittest.TestCase):
    def test_keyboard_interrupt_exits_without_a_traceback(self) -> None:
        with patch("chaos_agent.cli.configure_windows_utf8_stdio"), patch(
            "chaos_agent.cli.run", new=Mock(return_value=object())
        ), patch("chaos_agent.cli.asyncio.run", side_effect=KeyboardInterrupt):
            self.assertEqual(cli.main(), 130)
