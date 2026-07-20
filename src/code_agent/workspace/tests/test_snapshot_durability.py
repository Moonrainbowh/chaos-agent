from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import code_agent.workspace._atomic_artifact_write as atomic_module  # noqa: E402
import code_agent.workspace._windows_artifact_handles as handle_module  # noqa: E402
import code_agent.workspace._windows_artifact_native as native_module  # noqa: E402
import code_agent.workspace._windows_artifact_write as windows_module  # noqa: E402
from code_agent.workspace._atomic_artifact_write import AtomicArtifactWriter  # noqa: E402
from code_agent.workspace._windows_artifact_write import WindowsArtifactWriter  # noqa: E402
from code_agent.workspace.errors import WorkspaceError  # noqa: E402


class PosixDirectoryDurabilityTests(unittest.TestCase):
    def test_directory_fsync_failure_is_reported_after_publish(self) -> None:
        writer = object.__new__(AtomicArtifactWriter)
        writer.root = Path("/product-state")
        writer.root_identity = (1, 2)
        writer.identities = {"manifests": (1, 2)}
        metadata = SimpleNamespace(st_dev=1, st_ino=2)
        failure = OSError("directory durability failed")

        with (
            patch.object(atomic_module.os, "open", side_effect=(10, 11, 12)),
            patch.object(atomic_module.os, "fstat", return_value=metadata),
            patch.object(atomic_module.os, "write", side_effect=lambda _, data: len(data)),
            patch.object(atomic_module.os, "fsync", side_effect=(None, failure)) as fsync,
            patch.object(atomic_module.os, "replace") as replace,
            patch.object(atomic_module.os, "close"),
        ):
            with self.assertRaises(WorkspaceError) as captured:
                writer._write_posix("manifests", "snapshot.json", b"payload")

        self.assertIs(captured.exception.__cause__, failure)
        replace.assert_called_once()
        self.assertEqual(fsync.call_args_list, [call(12), call(11)])


@unittest.skipUnless(os.name == "nt", "Windows handle semantics only")
class WindowsDirectoryDurabilityTests(unittest.TestCase):
    def test_directory_flush_reports_supported_and_unsupported_results(self) -> None:
        self.assertTrue(self._flush_result(True, 0))
        for error in (1, 5):
            with self.subTest(error=error):
                self.assertFalse(self._flush_result(False, error))

    def test_directory_flush_raises_unexpected_errors(self) -> None:
        with self.assertRaises(OSError) as captured:
            self._flush_result(False, 6)

        self.assertEqual(captured.exception.winerror, 6)

    def _flush_result(self, result: bool, error: int) -> bool:
        function = Mock(return_value=result)
        with (
            patch.object(handle_module, "kernel_function", return_value=function),
            patch.object(handle_module.ctypes, "get_last_error", return_value=error),
        ):
            return handle_module.flush_directory(7)


@unittest.skipUnless(os.name == "nt", "Windows handle semantics only")
class WindowsWriterDurabilityTests(unittest.TestCase):
    def test_writer_records_when_directory_flush_is_unsupported(self) -> None:
        root = Path("C:/product-state")
        parents = {"manifests": root / "manifests"}
        with patch.object(windows_module, "directory_identity", return_value=(1, 2)):
            writer = WindowsArtifactWriter(root, parents)

        self.assertIsNone(writer.directory_flush_supported)
        with self.assertRaises(AttributeError):
            writer.directory_flush_supported = True  # type: ignore[misc]
        with (
            patch.object(windows_module, "open_directory", side_effect=(10, 11)),
            patch.object(windows_module, "verify_directory"),
            patch.object(windows_module, "_before_relative_write"),
            patch.object(windows_module, "_atomic_relative_write"),
            patch.object(windows_module, "flush_directory", return_value=False),
            patch.object(windows_module, "close_handle"),
        ):
            writer.write("manifests", parents["manifests"] / "snapshot.json", b"x")

        self.assertFalse(writer.directory_flush_supported)


@unittest.skipUnless(os.name == "nt", "Windows handle semantics only")
class WindowsCleanupTests(unittest.TestCase):
    def test_mark_delete_returns_ntstatus_result_without_raising(self) -> None:
        for status, expected in ((0, True), (-1, False)):
            with self.subTest(status=status):
                function = Mock(return_value=status)
                with patch.object(native_module, "nt_function", return_value=function):
                    self.assertIs(native_module.mark_delete(9), expected)

    def test_failed_cleanup_does_not_replace_rename_error(self) -> None:
        failure = OSError("rename failed")
        with (
            patch.object(windows_module, "create_relative_file", return_value=9),
            patch.object(windows_module, "write_file"),
            patch.object(windows_module, "flush_file"),
            patch.object(windows_module, "rename_relative", side_effect=failure),
            patch.object(windows_module, "mark_delete", return_value=False) as cleanup,
            patch.object(windows_module, "close_handle"),
        ):
            with self.assertRaises(OSError) as captured:
                windows_module._atomic_relative_write(7, "snapshot.json", b"x")

        self.assertIs(captured.exception, failure)
        cleanup.assert_called_once_with(9)


if __name__ == "__main__":
    unittest.main()
