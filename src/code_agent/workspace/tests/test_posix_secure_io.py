from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@unittest.skipUnless(os.name == "posix", "POSIX handle-relative I/O")
class PosixHandleRelativeTests(unittest.TestCase):
    def load_api(self):
        try:
            from code_agent.workspace import _posix_io
        except ImportError as error:
            self.fail(f"POSIX handle-relative I/O helper is missing: {error}")
        return _posix_io

    def test_open_read_uses_dir_fd_and_nofollow(self) -> None:
        api = self.load_api()
        with patch.object(api.os, "open", return_value=17) as opened:
            descriptor = api.open_read(11, "module.py")

        self.assertEqual(descriptor, 17)
        flags = opened.call_args.args[1]
        self.assertTrue(flags & api.O_NOFOLLOW)
        self.assertEqual(opened.call_args.kwargs["dir_fd"], 11)

    def test_mutations_are_handle_relative(self) -> None:
        api = self.load_api()
        with patch.object(api.os, "mkdir") as mkdir:
            api.mkdir(11, "nested")
        with patch.object(api.os, "replace") as replace:
            api.replace(11, "temp", "target")
        with patch.object(api.os, "unlink") as unlink:
            api.unlink(11, "target")

        self.assertEqual(mkdir.call_args.kwargs["dir_fd"], 11)
        self.assertEqual(replace.call_args.kwargs["src_dir_fd"], 11)
        self.assertEqual(replace.call_args.kwargs["dst_dir_fd"], 11)
        self.assertEqual(unlink.call_args.kwargs["dir_fd"], 11)


if __name__ == "__main__":
    unittest.main()
