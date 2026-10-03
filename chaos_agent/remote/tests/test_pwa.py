import shutil
import subprocess
import unittest
from pathlib import Path


class PwaScriptTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for PWA script behavior checks")
    def test_real_page_script_navigation_history_and_connection_lifecycle(self):
        result = subprocess.run([shutil.which("node"), str(Path(__file__).with_suffix(".js"))], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
