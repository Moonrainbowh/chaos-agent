import unittest
from unittest.mock import patch

from code_agent_win import bootstrap


class BootstrapTests(unittest.TestCase):
    def test_only_tui_options_enable_splash(self):
        for values in ([], ["--model", "fast"], ["--attach", "x"]):
            self.assertTrue(bootstrap._interactive(values))
        for values in (["acp"], ["--help"], ["run", "--json", "x"], ["--model"]):
            self.assertFalse(bootstrap._interactive(values))

    def test_failure_cleans_up_splash(self):
        with patch.object(bootstrap, "StartupSplash") as splash, patch(
            "sys.argv", ["chaos-agent"]
        ), patch("sys.stdin.isatty", return_value=True), patch(
            "code_agent_win.cli.main", side_effect=KeyboardInterrupt
        ):
            self.assertEqual(bootstrap.main(), 130)
        splash.return_value.start.assert_called_once()
        splash.return_value.stop.assert_called_once()
