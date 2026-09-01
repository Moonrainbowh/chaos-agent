from __future__ import annotations

import os
import unittest

from code_agent.context.models import FileSignature
from code_agent.workspace.errors import CodeSliceStaleError
from code_agent.workspace.files import CodeSliceRequest

try:
    from ._files_test_support import WorkspaceFilesTestCase
except ImportError:
    from _files_test_support import WorkspaceFilesTestCase


class BatchCodeSliceTests(WorkspaceFilesTestCase):
    def signature(self, path: str) -> FileSignature:
        metadata = (self.root / path).stat()
        return FileSignature.from_stat(metadata)

    def request(self, path: str, start: int, end: int) -> CodeSliceRequest:
        signature = self.signature(path)
        return CodeSliceRequest(
            path, start, end, signature.size_bytes, signature.modified_ns,
            signature.device_id, signature.file_id,
        )

    def test_reads_multiple_crlf_ranges_with_format_metadata(self) -> None:
        (self.root / "a.py").write_bytes(b"one\r\ntwo\r\nthree\r\n")
        (self.root / "b.py").write_text("alpha\nbeta\n", encoding="utf-8")

        slices = self.files().read_code_slices((
            self.request("a.py", 2, 3),
            self.request("b.py", 1, 1),
        ))

        self.assertEqual([item.path for item in slices], ["a.py", "b.py"])
        self.assertEqual(slices[0].text, "two\nthree\n")
        self.assertEqual(slices[0].text_format.newline, "crlf")
        self.assertEqual(slices[1].text_format.encoding, "utf-8")

    def test_stale_signature_fails_the_entire_batch(self) -> None:
        (self.root / "a.py").write_text("one\n", encoding="utf-8")
        (self.root / "b.py").write_text("two\n", encoding="utf-8")
        first = self.request("a.py", 1, 1)
        second = self.request("b.py", 1, 1)
        (self.root / "b.py").write_text("changed\n", encoding="utf-8")

        with self.assertRaises(CodeSliceStaleError):
            self.files().read_code_slices((first, second))

    def test_file_identity_mismatch_fails_even_when_size_and_mtime_match(self) -> None:
        (self.root / "a.py").write_text("one\n", encoding="utf-8")
        request = self.request("a.py", 1, 1)
        replaced = CodeSliceRequest(
            request.path,
            request.start_line,
            request.end_line,
            request.expected_size_bytes,
            request.expected_modified_ns,
            request.expected_device_id,
            request.expected_file_id + 1,
        )
        with self.assertRaises(CodeSliceStaleError):
            self.files().read_code_slices((replaced,))

    def test_rejects_invalid_batch_limits_and_overlapping_ranges(self) -> None:
        (self.root / "a.py").write_text("x\n" * 500, encoding="utf-8")
        request = self.request("a.py", 1, 1)
        with self.assertRaises(ValueError):
            self.files().read_code_slices(())
        with self.assertRaises(ValueError):
            self.files().read_code_slices((request,) * 17)
        with self.assertRaises(ValueError):
            self.files().read_code_slices((self.request("a.py", 1, 401),))
        with self.assertRaises(ValueError):
            self.files().read_code_slices((
                self.request("a.py", 1, 4), self.request("a.py", 4, 8)
            ))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_link_target_is_rejected_by_workspace_guard(self) -> None:
        outside = self.root.parent / f"{self.root.name}-outside.py"
        outside.write_text("secret\n", encoding="utf-8")
        try:
            try:
                os.symlink(outside, self.root / "linked.py")
            except OSError as error:
                self.skipTest(f"symlink creation unavailable: {error}")
            metadata = outside.stat()
            request = CodeSliceRequest(
                "linked.py", 1, 1, metadata.st_size, metadata.st_mtime_ns
            )
            with self.assertRaises(Exception):
                self.files().read_code_slices((request,))
        finally:
            outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
