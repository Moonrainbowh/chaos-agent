"""Subject observation inherits an explicit Host flag, never remote approval text."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from code_agent.core.models import ActionRequest, ActionResult
from code_agent.core.task import TaskAuthorization, TaskContract
from code_agent.core.task_state import TaskState
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.verification.task_service import LedgerTaskVerificationService
from code_agent.workspace.errors import SensitivePathError, PathOutsideWorkspace


class SubjectGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.container = Path(self.temp.name).resolve()
        self.root = self.container / 'workspace'
        self.root.mkdir()
        self.database = self.container / 'state.sqlite3'
        assert self.database.is_absolute() and self.database.parent == self.container
        self.sessions = SQLiteSessionRepository(self.database)
        thread = await self.sessions.create_thread()
        self.task = await self.sessions.create_task(thread,
            TaskContract('modify fixture configuration', TaskAuthorization.local_workspace(str(self.root))))
        (self.root / '.env').write_text('FIXTURE_ONLY=old', encoding='utf-8')

    async def asyncTearDown(self):
        self.sessions.close()
        self.temp.cleanup()

    async def test_default_prepare_and_logical_commit_reject_sensitive_subject(self):
        service = LedgerTaskVerificationService(self.root, self.sessions)
        changed = replace(TaskState.empty(), files_changed=('.env',))
        with self.assertRaises(SensitivePathError):
            await service.prepare(self.task, changed)
        initial = await service.prepare(self.task, TaskState.empty())
        service.begin_logical_change(self.task.id)
        pending = await service.record_action(self.task, ActionRequest('edit', 'write_file', {'path': '.env'}),
            ActionResult('edit', 'write_file', {'path': '.env'}), replace(initial, files_changed=('.env',)))
        with self.assertRaises(SensitivePathError):
            await service.commit_logical_change(self.task, pending)
        self.assertEqual((await self.sessions.load_task_state(self.task.thread_id)).code_generation, 0)

    async def test_explicit_host_flag_snapshots_sensitive_change_without_verification_pass(self):
        service = LedgerTaskVerificationService(self.root, self.sessions, allow_sensitive_paths=True)
        initial = await service.prepare(self.task, replace(TaskState.empty(), files_changed=('.env',)))
        service.begin_logical_change(self.task.id)
        (self.root / '.env').write_text('FIXTURE_ONLY=new', encoding='utf-8')
        pending = await service.record_action(self.task, ActionRequest('edit', 'write_file', {'path': '.env'}),
            ActionResult('edit', 'write_file', {'path': '.env'}), initial)
        self.assertEqual(pending.code_generation, 0)
        committed, plan = await service.commit_logical_change(self.task, pending)
        self.assertEqual(committed.code_generation, 1)
        self.assertNotEqual(committed.subject_hash, initial.subject_hash)
        self.assertEqual(plan.changed_files, ('.env',))
        self.assertEqual(await self.sessions.list_completed_verification_evidence(self.task.id), ())
        self.assertEqual((await self.sessions.load_task_state(self.task.thread_id)).subject_hash, committed.subject_hash)

    async def test_explicit_sensitive_flag_never_allows_outside_subject(self):
        outside = self.container / 'outside.txt'
        outside.write_text('OUTSIDE_FIXTURE', encoding='utf-8')
        service = LedgerTaskVerificationService(self.root, self.sessions, allow_sensitive_paths=True)
        for path in ('../outside.txt', str(outside)):
            with self.subTest(path=path), self.assertRaises(PathOutsideWorkspace):
                await service.prepare(self.task, replace(TaskState.empty(), files_changed=(path,)))

    async def test_sensitive_capability_requires_strict_bool(self):
        for flag in (1, 0, 'true', None, {}, []):
            with self.subTest(flag=flag), self.assertRaises(TypeError):
                LedgerTaskVerificationService(self.root, self.sessions, allow_sensitive_paths=flag)


if __name__ == '__main__':
    unittest.main()
