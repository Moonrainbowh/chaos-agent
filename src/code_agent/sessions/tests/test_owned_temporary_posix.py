import os
import tempfile
import unittest
from pathlib import Path

from code_agent._owned_temporary import OwnedTemporary, OwnedTemporaryReplacedError


@unittest.skipIf(os.name == "nt", "POSIX inode and timestamp semantics")
class OwnedTemporaryPosixTests(unittest.TestCase):
    def test_write_and_hardlink_do_not_change_owned_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "temporary"
            path.touch()
            owned = OwnedTemporary.capture(path)
            path.write_bytes(b"database")
            destination = Path(folder) / "published"
            destination.hardlink_to(path)
            owned.cleanup()
            self.assertFalse(path.exists())
            self.assertEqual(destination.read_bytes(), b"database")

    def test_replacement_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "temporary"
            path.write_bytes(b"ours")
            owned = OwnedTemporary.capture(path)
            path.unlink()
            path.write_bytes(b"foreign")
            with self.assertRaises(OwnedTemporaryReplacedError):
                owned.cleanup()
            self.assertEqual(path.read_bytes(), b"foreign")
