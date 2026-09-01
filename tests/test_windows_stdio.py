from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from code_agent_win.stdio import configure_windows_utf8_stdio


class WindowsStdioTests(unittest.TestCase):
    def test_windows_streams_are_reconfigured_to_utf8(self) -> None:
        stdout = Mock()
        stderr = Mock()

        with patch("code_agent_win.stdio.os.name", "nt"):
            configure_windows_utf8_stdio(stdout, stderr)

        stdout.reconfigure.assert_called_once_with(
            encoding="utf-8", errors="backslashreplace"
        )
        stderr.reconfigure.assert_called_once_with(
            encoding="utf-8", errors="backslashreplace"
        )

    def test_non_windows_and_streams_without_reconfigure_are_safe(self) -> None:
        stream = object()
        with patch("code_agent_win.stdio.os.name", "posix"):
            configure_windows_utf8_stdio(stream, stream)
        with patch("code_agent_win.stdio.os.name", "nt"):
            configure_windows_utf8_stdio(stream, stream)


if __name__ == "__main__":
    unittest.main()
