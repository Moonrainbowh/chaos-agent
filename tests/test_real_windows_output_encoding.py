from __future__ import annotations

import base64
import ctypes
import os
import sys
import tempfile
import unittest
from pathlib import Path

from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest
from code_agent.runtime.local import WindowsLocalRuntime
from code_agent_win.process_actions import run_process_action


def windows_code_page(*, oem: bool) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel32.GetOEMCP if oem else kernel32.GetACP
    function.argtypes = ()
    function.restype = ctypes.c_uint32
    return int(function())


def non_ascii_sample(codec: str) -> tuple[str, bytes]:
    candidates = ("中文", "日本語", "한국", "café", "Привет", "مرحبا", "ไทย")
    for text in candidates:
        try:
            raw = text.encode(codec, errors="strict")
        except UnicodeEncodeError:
            continue
        if raw.decode(codec, errors="strict") == text:
            return text, raw
    raise unittest.SkipTest(f"no non-ASCII round-trip sample for {codec}")


@unittest.skipUnless(os.name == "nt", "Windows code pages are Windows-only")
class RealWindowsOutputEncodingTests(unittest.IsolatedAsyncioTestCase):
    async def test_structured_process_decodes_real_ansi_and_oem_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            runtime = WindowsLocalRuntime(Path(raw_root).resolve())
            for encoding, oem in (("windows-ansi", False), ("windows-oem", True)):
                with self.subTest(encoding=encoding):
                    page = windows_code_page(oem=oem)
                    text, raw = non_ascii_sample(f"cp{page}")
                    payload = base64.b64encode(raw).decode("ascii")
                    script = (
                        "import base64,sys;"
                        f"sys.stdout.buffer.write(base64.b64decode('{payload}'))"
                    )
                    request = ActionRequest(
                        encoding,
                        "run_process_v1",
                        {
                            "program": sys.executable,
                            "args": ["-c", script],
                            "stdout_encoding": encoding,
                        },
                    )

                    result = await run_process_action(
                        request, runtime, CancellationToken(), None
                    )

                    self.assertFalse(result.is_error)
                    self.assertEqual(result.output["stdout"], text)
                    self.assertEqual(result.output["stdout_code_page"], page)
                    self.assertNotIn("stdout_base64", result.output)


if __name__ == "__main__":
    unittest.main()
