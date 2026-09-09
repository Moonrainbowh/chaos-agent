import io
import os
import re
import unittest
from unittest.mock import patch

from code_agent.interfaces.startup_splash import StartupSplash
from code_agent.interfaces.startup_logo import scan_frame


class StartupSplashTests(unittest.TestCase):
    def test_redirected_output_is_untouched(self):
        stream = io.StringIO()
        splash = StartupSplash(stream)
        splash.start()
        splash.stop()
        self.assertEqual(stream.getvalue(), "")

    def test_static_feedback_clears_and_restores_once(self):
        for env in ({"NO_COLOR": ""}, {"CHAOS_REDUCED_MOTION": "1"}):
            with self.subTest(env=env), patch.dict(os.environ, env):
                stream = io.StringIO()
                stream.isatty = lambda: True
                with patch("code_agent.interfaces.startup_splash._enable_vt") as vt:
                    splash = StartupSplash(stream)
                    splash.start()
                    splash.stop()
                    splash.stop()
                    vt.return_value.assert_called_once_with()
                output = stream.getvalue()
                self.assertIn("\x1b[?1049h", output)
                self.assertTrue(output.endswith("\x1b[?1049l\x1b[2J\x1b[3J\x1b[H\x1b[?25h"))
                self.assertEqual(output.count("\x1b[3J"), 1)
                self.assertIsNone(splash._thread)
                if "NO_COLOR" in env:
                    self.assertNotIn("38;2", output)

    def test_animation_stops_without_waiting_for_full_fade(self):
        stream = io.StringIO()
        stream.isatty = lambda: True
        with patch.dict(os.environ, {}, clear=True), patch(
            "code_agent.interfaces.startup_splash._enable_vt"
        ):
            splash = StartupSplash(stream)
            splash.start()
            splash.stop()
            self.assertFalse(splash._thread.is_alive())
            self.assertTrue(stream.getvalue().endswith("\x1b[?1049l\x1b[2J\x1b[3J\x1b[H\x1b[?25h"))

    def test_scan_reveals_only_left_columns_and_reaches_brand_blue(self):
        whole = scan_frame(100, 30, 1)
        half = scan_frame(100, 30, .5)
        self.assertIn("125;211;252", whole)
        self.assertEqual(scan_frame(100, 30, 2), whole)
        self.assertGreater(whole.count("█"), half.count("█"))
        # Every displayed row is a left prefix of its completed counterpart.
        pattern = r"\x1b\[\d+;\d+H"
        strip = lambda s: re.sub(r"\x1b\[[\d;]*m", "", s)
        for partial, complete in zip(re.split(pattern, strip(half))[1:], re.split(pattern, strip(whole))[1:]):
            self.assertEqual(partial[:33], complete[:33])
            self.assertFalse(partial[33:].strip())

    def test_narrow_and_short_frames_stay_inside_screen(self):
        for width, height in ((1, 1), (5, 2), (30, 8), (40, 10), (80, 24), (180, 50)):
            with self.subTest(size=(width, height)):
                frame = scan_frame(width, height, 1, color=False)
                chunks = re.findall(r"\x1b\[(\d+);(\d+)H([^\x1b]*)", frame)
                for row, col, text in chunks:
                    self.assertLessEqual(int(row), height)
                    self.assertLessEqual(int(col) - 1 + len(text), max(0, width - 1))

    def test_write_failure_still_restores_console_mode(self):
        stream = io.StringIO()
        stream.isatty = lambda: True
        with patch.dict(os.environ, {"NO_COLOR": "1"}), patch(
            "code_agent.interfaces.startup_splash._enable_vt"
        ) as vt:
            splash = StartupSplash(stream)
            splash.start()
            with patch.object(splash, "_write", side_effect=OSError("closed")):
                with self.assertRaises(OSError):
                    splash.stop()
            vt.return_value.assert_called_once_with()
