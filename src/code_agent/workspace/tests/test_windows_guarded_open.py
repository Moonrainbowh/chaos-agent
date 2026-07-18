from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import call, patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace import _windows_guarded_open as windows_open  # noqa: E402
from code_agent.workspace.errors import WorkspaceError  # noqa: E402


@unittest.skipUnless(os.name == "nt", "Windows handle semantics")
class ParentChainSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(r"C:\workspace")
        self.expected = self.root / "one" / "two" / "file.txt"
        self.paths = (
            Path(r"C:\\"),
            self.root,
            self.root / "one",
            self.root / "one" / "two",
            self.expected,
        )

    def test_parents_deny_delete_share_and_remain_open_through_leaf(self) -> None:
        closed: list[int] = []
        handles = iter((11, 12, 13, 14, 15))

        def create(path: Path, **_options: int) -> int:
            if path == self.expected:
                self.assertEqual(closed, [])
            return next(handles)

        with self._safe_patches(create, closed) as create_mock:
            descriptor = windows_open.open_guarded_file(
                self.expected, self.root
            )

        self.assertEqual(descriptor, 9)
        self.assertEqual([item.args[0] for item in create_mock.call_args_list], list(self.paths))
        for parent_call in create_mock.call_args_list[:-1]:
            self.assertFalse(parent_call.kwargs["share_mode"] & 0x2)
            self.assertFalse(parent_call.kwargs["share_mode"] & 0x4)
            self.assertTrue(parent_call.kwargs["flags"] & 0x00200000)
            self.assertTrue(parent_call.kwargs["flags"] & 0x02000000)
        self.assertEqual(closed, [14, 13, 12, 11])

    def test_parent_share_blocks_in_place_reparse_attack(self) -> None:
        closed: list[int] = []
        handles = iter((11, 12, 13, 14, 15))
        parent_mutated = False

        def create(path: Path, **options: int) -> int:
            nonlocal parent_mutated
            if path != self.expected and options["share_mode"] & 0x2:
                parent_mutated = True
            if path == self.expected and parent_mutated:
                raise windows_open._WindowsOpenFailure(2, path)
            return next(handles)

        with self._safe_patches(create, closed):
            descriptor = windows_open.open_guarded_file(
                self.expected, self.root
            )

        self.assertEqual(descriptor, 9)
        self.assertFalse(parent_mutated)
        self.assertEqual(closed, [14, 13, 12, 11])

    def test_external_leaf_stabilizes_every_parent_from_volume_anchor(self) -> None:
        external = Path("D:/approved/deep/file.txt")

        parents = windows_open._parent_paths(external, self.root)

        self.assertEqual(
            parents,
            (
                Path("D:/"),
                Path("D:/approved"),
                Path("D:/approved/deep"),
            ),
        )

    def test_parent_not_found_is_workspace_error_not_leaf_missing(self) -> None:
        failure = windows_open._WindowsOpenFailure(2, self.paths[1])

        def create(path: Path, **_options: int) -> int:
            if path == self.paths[0]:
                return 11
            raise failure

        closed: list[int] = []
        with self._safe_patches(create, closed):
            with self.assertRaises(WorkspaceError) as raised:
                windows_open.open_guarded_file(self.expected, self.root)

        self.assertNotIsInstance(
            raised.exception, windows_open._WindowsLeafMissingError
        )
        self.assertEqual(closed, [11])

    def test_reparse_or_misdirected_parent_never_reaches_leaf(self) -> None:
        cases = ((0x410, self.root), (0x10, Path(r"C:\outside")))

        for attributes, final_path in cases:
            with self.subTest(attributes=attributes, final_path=final_path):
                closed: list[int] = []
                with (
                    patch.object(
                        windows_open, "_create_handle", return_value=11
                    ) as create,
                    patch.object(
                        windows_open, "_handle_attributes", return_value=attributes
                    ),
                    patch.object(
                        windows_open, "_final_handle_path", return_value=final_path
                    ),
                    patch.object(
                        windows_open,
                        "_close_handle",
                        side_effect=lambda handle: closed.append(handle),
                    ),
                ):
                    with self.assertRaises(WorkspaceError) as raised:
                        windows_open.open_guarded_file(self.expected, self.root)

                self.assertNotIsInstance(
                    raised.exception, windows_open._WindowsLeafMissingError
                )
                self.assertEqual(create.call_count, 1)
                self.assertEqual(closed, [11])

    def test_leaf_missing_after_stable_parent_chain_remains_missing(self) -> None:
        for error_code in (2, 3):
            with self.subTest(error_code=error_code):
                handles = iter((11, 12, 13, 14))

                def create(path: Path, **_options: int) -> int:
                    if path == self.expected:
                        raise windows_open._WindowsOpenFailure(error_code, path)
                    return next(handles)

                closed: list[int] = []
                with self._safe_patches(create, closed):
                    with self.assertRaises(windows_open._WindowsLeafMissingError):
                        windows_open.open_guarded_file(self.expected, self.root)

                self.assertEqual(closed, [14, 13, 12, 11])

    def test_reparse_leaf_is_closed_without_transfer(self) -> None:
        closed: list[int] = []
        handles = iter((11, 12, 13, 14, 15))

        def attributes(handle: int) -> int:
            return 0x400 if handle == 15 else 0x10

        with (
            self._safe_patches(
                lambda _path, **_options: next(handles), closed
            ),
            patch.object(
                windows_open, "_handle_attributes", side_effect=attributes
            ),
            patch.object(windows_open, "_handle_to_descriptor") as transfer,
        ):
            with self.assertRaises(WorkspaceError) as raised:
                windows_open.open_guarded_file(self.expected, self.root)

        self.assertNotIsInstance(
            raised.exception, windows_open._WindowsLeafMissingError
        )
        transfer.assert_not_called()
        self.assertEqual(closed, [15, 14, 13, 12, 11])

    def test_failed_leaf_transfer_releases_leaf_and_parents(self) -> None:
        closed: list[int] = []
        handles = iter((11, 12, 13, 14, 15))

        with (
            self._safe_patches(
                lambda _path, **_options: next(handles), closed
            ),
            patch.object(
                windows_open,
                "_handle_to_descriptor",
                side_effect=FileNotFoundError("post-open race"),
            ),
        ):
            with self.assertRaises(WorkspaceError) as raised:
                windows_open.open_guarded_file(self.expected, self.root)

        self.assertNotIsInstance(
            raised.exception, windows_open._WindowsLeafMissingError
        )
        self.assertEqual(closed, [15, 14, 13, 12, 11])

    def test_parent_close_failure_preserves_primary_and_closes_all(self) -> None:
        closed: list[int] = []
        handles = iter((11, 12, 13, 14))

        def create(path: Path, **_options: int) -> int:
            if path == self.expected:
                raise windows_open._WindowsOpenFailure(5, path)
            return next(handles)

        def close(handle: int) -> None:
            closed.append(handle)
            if handle == 13:
                raise OSError("close failed")

        with self._safe_patches(create, closed, close=close):
            with self.assertRaisesRegex(WorkspaceError, "cannot open guarded leaf"):
                windows_open.open_guarded_file(self.expected, self.root)

        self.assertEqual(closed, [14, 13, 12, 11])

    def test_parent_close_failure_after_success_closes_descriptor(self) -> None:
        closed: list[int] = []
        close_descriptor = unittest.mock.Mock()
        handles = iter((11, 12, 13, 14, 15))

        def close(handle: int) -> None:
            closed.append(handle)
            if handle == 13:
                raise OSError("close failed")

        with (
            self._safe_patches(
                lambda _path, **_options: next(handles), closed, close=close
            ),
            patch.object(windows_open.os, "close", close_descriptor),
        ):
            with self.assertRaisesRegex(
                WorkspaceError, "cannot close guarded parent"
            ):
                windows_open.open_guarded_file(self.expected, self.root)

        close_descriptor.assert_called_once_with(9)

    def _safe_patches(self, create, closed: list[int], *, close=None):
        handle_paths = dict(zip((11, 12, 13, 14, 15), self.paths))
        close_effect = close or (lambda handle: closed.append(handle))
        return _PatchGroup(
            patch.object(windows_open, "_create_handle", side_effect=create),
            patch.object(
                windows_open,
                "_handle_attributes",
                side_effect=lambda handle: 0x80 if handle == 15 else 0x10,
            ),
            patch.object(
                windows_open,
                "_final_handle_path",
                side_effect=lambda handle: handle_paths[handle],
            ),
            patch.object(windows_open, "_handle_to_descriptor", return_value=9),
            patch.object(windows_open, "_close_handle", side_effect=close_effect),
        )


class _PatchGroup:
    def __init__(self, *patchers) -> None:
        self.patchers = patchers

    def __enter__(self):
        entered = [patcher.start() for patcher in self.patchers]
        return entered[0]

    def __exit__(self, *error) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()


if __name__ == "__main__":
    unittest.main()
