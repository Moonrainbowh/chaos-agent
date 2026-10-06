from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from code_agent.workspace import _batch_apply, _secure_replace
from code_agent.workspace.edits import WorkspaceEditor, BatchApplyResult, BatchApplyStatus
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace._secure_io import _inspect_path, same_path_state


class BatchOutputOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix='s14-owned-output-')
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.root = base / 'workspace'
        assert self.root.is_absolute() and self.root.is_relative_to(base)
        self.root.mkdir()
        self.editor = WorkspaceEditor(WorkspacePathGuard(self.root))

    def replace_foreign(self, target: Path) -> None:
        replacement = self.root / 'foreign.tmp'
        replacement.write_bytes(target.read_bytes())
        os.replace(replacement, target)
        self.foreign = _inspect_path(target, missing_ok=False, context='test')

    def test_same_bytes_replacement_between_publish_and_batch_observe_is_foreign(self):
        for existed in (False, True):
            with self.subTest(existed=existed):
                target = self.root / 'a.txt'
                target.unlink(missing_ok=True)
                if existed:
                    target.write_bytes(b'before')
                plan = self.editor.plan_batch((self.editor.plan_write('a.txt', 'after'),))
                original = _batch_apply.write_bytes_exact

                def write(*args, **kwargs):
                    receipt = original(*args, **kwargs)
                    self.replace_foreign(target)
                    return receipt

                with patch.object(_batch_apply, 'write_bytes_exact', side_effect=write):
                    result = self.editor.apply_batch(plan)
                self.assertEqual(result.status, BatchApplyStatus.PARTIAL_CONFLICT)
                self.assertEqual(target.read_bytes(), b'after')
                self.assertTrue(same_path_state(_inspect_path(target, missing_ok=False, context='test'), self.foreign))

    def test_committed_publication_error_never_claims_foreign_same_bytes(self):
        target = self.root / 'a.txt'
        target.write_bytes(b'before')
        plan = self.editor.plan_batch((self.editor.plan_write('a.txt', 'after'),))
        verify_name = '_verify_path_result' if os.name == 'nt' else '_verify_posix_result'

        def fail(*args, **kwargs):
            self.replace_foreign(target)
            raise OSError('fault after publish')

        with patch.object(_secure_replace, verify_name, side_effect=fail):
            result = self.editor.apply_batch(plan)
        self.assertEqual(result.status, BatchApplyStatus.PARTIAL_CONFLICT)
        self.assertEqual(target.read_bytes(), b'after')
        self.assertTrue(same_path_state(_inspect_path(target, missing_ok=False, context='test'), self.foreign))

    def test_normal_result_receipt_is_bound_and_immutable(self):
        plan = self.editor.plan_batch((self.editor.plan_write('a.txt', 'after'),))
        result = self.editor.apply_batch(plan)
        self.assertEqual(result.status, BatchApplyStatus.APPLIED)
        self.assertEqual(result.plan_id, plan.plan_id)
        receipt = dict(result.post_identities)
        self.assertTrue(same_path_state(receipt['a.txt'], _inspect_path(self.root / 'a.txt', missing_ok=False, context='test')))
        self.assertIsInstance(result.post_identities, tuple)
        with self.assertRaises(FrozenInstanceError):
            result.plan_id = 'forged'

    def test_later_failure_rolls_back_normal_owned_output(self):
        (self.root / 'a.txt').write_bytes(b'before')
        plan = self.editor.plan_batch((self.editor.plan_write('a.txt', 'after'), self.editor.plan_write('b.txt', 'second')))
        original = _batch_apply.write_bytes_exact

        def write(editor, expected, *args, **kwargs):
            if expected.relative_path == 'b.txt':
                raise InterruptedError('cancel fixture')
            return original(editor, expected, *args, **kwargs)

        with patch.object(_batch_apply, 'write_bytes_exact', side_effect=write):
            result = self.editor.apply_batch(plan)
        self.assertEqual(result.status, BatchApplyStatus.ROLLED_BACK)
        self.assertEqual((self.root / 'a.txt').read_bytes(), b'before')
        self.assertFalse((self.root / 'b.txt').exists())

    def test_legacy_result_defaults_to_no_ownership_proof(self):
        result = BatchApplyResult(BatchApplyStatus.APPLIED)
        self.assertIsNone(result.plan_id)
        self.assertEqual(result.post_identities, ())

    def test_actual_write_without_receipt_keeps_unprovable_output(self):
        target = self.root / 'a.txt'
        target.write_bytes(b'before')
        plan = self.editor.plan_batch((self.editor.plan_write('a.txt', 'after'),))
        original = _batch_apply.write_bytes_exact

        def write(*args, **kwargs):
            original(*args, **kwargs)
            return None

        with patch.object(_batch_apply, 'write_bytes_exact', side_effect=write):
            result = self.editor.apply_batch(plan)
        self.assertEqual(result.status, BatchApplyStatus.PARTIAL_CONFLICT)
        self.assertEqual(target.read_bytes(), b'after')

    def test_legacy_execution_return_without_receipt_is_partial_conflict(self):
        plan = self.editor.plan_batch((self.editor.plan_write('a.txt', 'after'),))
        original = _batch_apply._execute_operation

        def execute(*args, **kwargs):
            original(*args, **kwargs)
            return None

        with patch.object(_batch_apply, '_execute_operation', side_effect=execute):
            result = self.editor.apply_batch(plan)
        self.assertEqual(result.status, BatchApplyStatus.PARTIAL_CONFLICT)
        self.assertEqual((self.root / 'a.txt').read_bytes(), b'after')

    def test_committed_error_rolls_back_owned_output(self):
        target = self.root / 'a.txt'
        target.write_bytes(b'before')
        plan = self.editor.plan_batch((self.editor.plan_write('a.txt', 'after'),))
        verify_name = '_verify_path_result' if os.name == 'nt' else '_verify_posix_result'
        original = getattr(_secure_replace, verify_name)
        calls = 0

        def fail_once(*args, **kwargs):
            nonlocal calls
            original(*args, **kwargs)
            calls += 1
            if calls == 1:
                raise OSError('fault after actual publish')

        with patch.object(_secure_replace, verify_name, side_effect=fail_once):
            result = self.editor.apply_batch(plan)
        self.assertEqual(result.status, BatchApplyStatus.ROLLED_BACK)
        self.assertEqual(target.read_bytes(), b'before')

    def test_delete_receipt_records_explicit_missing_endpoint(self):
        (self.root / 'a.txt').write_bytes(b'before')
        plan = self.editor.plan_batch((self.editor.plan_delete('a.txt'),))
        result = self.editor.apply_batch(plan)
        self.assertEqual(result.status, BatchApplyStatus.APPLIED)
        self.assertEqual(result.post_identities, (('a.txt', None),))
        self.assertFalse((self.root / 'a.txt').exists())

    @unittest.skipUnless(os.name == 'nt', 'native Windows exact move')
    def test_normal_and_case_only_move_receipts_keep_complete_handle_state(self):
        for case_only in (False, True):
            with self.subTest(case_only=case_only):
                source = self.root / 'source.txt'
                source.write_bytes(b'moved bytes')
                destination = self.root / ('SOURCE.txt' if case_only else 'moved.txt')
                plan = self.editor.plan_batch((self.editor.plan_move(source.name, destination.name),))
                result = self.editor.apply_batch(plan)
                self.assertEqual(result.status, BatchApplyStatus.APPLIED)
                receipt = dict(result.post_identities)
                actual = _inspect_path(destination, missing_ok=False, context='test')
                self.assertTrue(same_path_state(receipt[destination.name], actual))
                if case_only:
                    self.assertTrue(same_path_state(receipt[source.name], actual))
                else:
                    self.assertIsNone(receipt[source.name])
                destination.unlink()

    @unittest.skipUnless(os.name == 'nt', 'native Windows exact move')
    def test_native_move_same_byte_replacement_before_observe_is_foreign(self):
        for case_only in (False, True):
            with self.subTest(case_only=case_only):
                source = self.root / 'source.txt'
                source.write_bytes(b'moved bytes')
                destination = self.root / ('SOURCE.txt' if case_only else 'moved.txt')
                plan = self.editor.plan_batch((self.editor.plan_move(source.name, destination.name),))
                original = _batch_apply.move_path_exact

                def move(*args, **kwargs):
                    receipt = original(*args, **kwargs)
                    self.replace_foreign(destination)
                    return receipt

                with patch.object(_batch_apply, 'move_path_exact', side_effect=move):
                    result = self.editor.apply_batch(plan)
                self.assertEqual(result.status, BatchApplyStatus.PARTIAL_CONFLICT)
                self.assertTrue(same_path_state(_inspect_path(destination, missing_ok=False, context='test'), self.foreign))
                destination.unlink()

    @unittest.skipUnless(os.name == 'nt', 'native Windows exact move')
    def test_native_move_post_rename_error_rolls_back_same_handle_object(self):
        from code_agent.workspace import _windows_exact_move

        source = self.root / 'source.txt'
        source.write_bytes(b'moved bytes')
        plan = self.editor.plan_batch((self.editor.plan_move('source.txt', 'moved.txt'),))
        original = _windows_exact_move._final_handle_path
        calls = 0

        def fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError('fault after actual native rename')
            return original(*args, **kwargs)

        with patch.object(_windows_exact_move, '_final_handle_path', side_effect=fail_once):
            result = self.editor.apply_batch(plan)
        self.assertEqual(result.status, BatchApplyStatus.ROLLED_BACK)
        self.assertEqual(source.read_bytes(), b'moved bytes')
        self.assertFalse((self.root / 'moved.txt').exists())
