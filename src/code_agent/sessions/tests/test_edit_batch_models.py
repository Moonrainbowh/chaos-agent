from __future__ import annotations

import unittest

from code_agent.sessions.edit_batch_models import (
    EditBatchOperation,
    EditBatchOperationKind,
    EditBatchPath,
    EditBatchPrepare,
)
from code_agent.sessions.rewind_models import (
    CoverageToken,
    RewindBaseline,
    RewindMutationPath,
    RewindMutationPrepare,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
SIZE_A = 7
SIZE_B = 11


class EditBatchModelTests(unittest.TestCase):
    def test_path_sizes_follow_existence(self) -> None:
        path = EditBatchPath(
            "note.txt", True, HASH_A, SIZE_A, True, HASH_B, SIZE_B
        )
        self.assertEqual(
            (path.before_size, path.after_size), (SIZE_A, SIZE_B)
        )
        invalid = (
            lambda: EditBatchPath(
                "note.txt", False, None, 1, True, HASH_B, SIZE_B
            ),
            lambda: EditBatchPath(
                "note.txt", True, HASH_A, -1, True, HASH_B, SIZE_B
            ),
            lambda: EditBatchPath(
                "note.txt", True, HASH_A, True, True, HASH_B, SIZE_B
            ),
        )
        for build in invalid:
            with self.subTest(case=build), self.assertRaises(
                (TypeError, ValueError)
            ):
                build()

    def test_move_records_both_endpoints_and_case_only_identity(self) -> None:
        source = EditBatchPath(
            "src/name.py", True, HASH_A, SIZE_A, False, None, 0
        )
        target = EditBatchPath(
            "src/NAME.py", False, None, 0, True, HASH_A, SIZE_A
        )
        operation = EditBatchOperation(
            EditBatchOperationKind.MOVE, source, target, case_only=True
        )
        self.assertTrue(operation.case_only)
        self.assertEqual(operation.source, source)
        self.assertEqual(operation.target, target)

    def test_operation_shapes_fail_closed(self) -> None:
        existing = EditBatchPath(
            "a.txt", True, HASH_A, SIZE_A, True, HASH_B, SIZE_B
        )
        missing = EditBatchPath(
            "b.txt", False, None, 0, True, HASH_A, SIZE_A
        )
        cases = (
            lambda: EditBatchOperation(
                EditBatchOperationKind.WRITE, existing, existing
            ),
            lambda: EditBatchOperation(
                EditBatchOperationKind.MOVE, None, missing
            ),
            lambda: EditBatchOperation(
                EditBatchOperationKind.CREATE, None, existing
            ),
            lambda: EditBatchOperation(
                EditBatchOperationKind.MOVE,
                EditBatchPath(
                    "a.txt", True, HASH_A, SIZE_A, False, None, 0
                ),
                EditBatchPath(
                    "b.txt", True, HASH_B, SIZE_B, True, HASH_A, SIZE_A
                ),
            ),
            lambda: EditBatchOperation(
                EditBatchOperationKind.MOVE,
                EditBatchPath(
                    "a.txt", True, HASH_A, SIZE_A, False, None, 0
                ),
                missing,
                case_only=True,
            ),
            lambda: EditBatchOperation(
                EditBatchOperationKind.MOVE,
                EditBatchPath(
                    "straße.py", True, HASH_A, SIZE_A, False, None, 0
                ),
                EditBatchPath(
                    "strasse.py", False, None, 0, True, HASH_A, SIZE_A
                ),
                case_only=True,
            ),
            lambda: EditBatchOperation(
                EditBatchOperationKind.MOVE,
                EditBatchPath(
                    "source.py", True, HASH_A, SIZE_A, False, None, 0
                ),
                EditBatchPath(
                    "target.py", False, None, 0, True, HASH_A, SIZE_B
                ),
            ),
        )
        for build in cases:
            with self.subTest(case=build), self.assertRaises(ValueError):
                build()

    def test_case_only_prepare_uses_one_parent_source_projection(self) -> None:
        source = EditBatchPath(
            "src/name.py", True, HASH_A, SIZE_A, False, None, 0
        )
        target = EditBatchPath(
            "src/NAME.py", False, None, 0, True, HASH_A, SIZE_A
        )
        operation = EditBatchOperation(
            EditBatchOperationKind.MOVE, source, target, case_only=True
        )
        mutation = RewindMutationPrepare(
            CoverageToken("c" * 64, 1),
            "owner",
            "owner",
            None,
            None,
            "request",
            "edit_batch",
            {"paths": ["src/name.py"]},
            (
                RewindMutationPath(
                    "src/name.py",
                    True,
                    HASH_A,
                    RewindBaseline.GIT_UNSTAGED,
                    True,
                    HASH_A,
                ),
            ),
        )
        prepared = EditBatchPrepare(
            mutation, "case-plan", "d" * 64, (operation,)
        )
        self.assertEqual(prepared.operations, (operation,))

    def test_prepare_rejects_windows_aliases_across_operations(self) -> None:
        operations = tuple(
            EditBatchOperation(
                EditBatchOperationKind.WRITE,
                None,
                EditBatchPath(
                    path, True, HASH_A, SIZE_A, True, HASH_B, SIZE_B
                ),
            )
            for path in ("A.txt", "a.txt")
        )
        mutation = RewindMutationPrepare(
            CoverageToken("c" * 64, 1),
            "owner",
            "owner",
            None,
            None,
            "request",
            "edit_batch",
            {"paths": ["A.txt", "a.txt"]},
            tuple(
                RewindMutationPath(
                    operation.target.path,
                    True,
                    HASH_A,
                    RewindBaseline.GIT_UNSTAGED,
                    True,
                    HASH_B,
                )
                for operation in operations
            ),
        )
        with self.assertRaisesRegex(ValueError, "overlapping"):
            EditBatchPrepare(mutation, "plan", "d" * 64, operations)


if __name__ == "__main__":
    unittest.main()
