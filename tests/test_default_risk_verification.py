"""Default Application lifecycle uses actual Host risk evidence and user decisions."""
import os
import unittest
from unittest.mock import patch

from code_agent.core.models import ModelEvent, ModelEventKind, ToolCall, Usage
from code_agent.core.task import TaskStatus
from code_agent.verification.evidence import EvidenceProvenance
from tests.test_tui_repair_integration import application_fixture


class ScriptModel:
    def __init__(self, write=True):
        self.calls, self.write = 0, write

    async def stream(self, *args):
        self.calls += 1
        if self.calls == 1:
            name = 'write_file' if self.write else 'read_file'
            args = {'path': 'README.md'}
            if self.write:
                args['content'] = 'New description.\n'
            yield ModelEvent(ModelEventKind.TOOL_CALL, tool_call=ToolCall('doc-action', name, args))
        else:
            yield ModelEvent(ModelEventKind.TEXT_DELTA, text=(
                'Description updated.' if self.write else
                'The existing description meets the request; no change is required.'))
        yield ModelEvent(ModelEventKind.USAGE, usage=Usage(10, 5))
        yield ModelEvent(ModelEventKind.COMPLETED)

    async def aclose(self):
        pass


class DefaultRiskVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def run_doc(self, app, *, write=True):
        root = app.workspace_root
        (root / 'README.md').write_text('Old description.\n', encoding='utf-8')
        app.controller._engine._model.model = ScriptModel(write)
        task = await app.tasks.start('Update README.md description')
        events = [event async for event in app.tasks.events(task.id)]
        return task, events, await app.tasks.result(task.id)

    async def test_default_document_change_uses_planner_not_unrelated_tests(self):
        with patch.dict(os.environ):
            os.environ.pop('CHAOS_STRUCTURED_VERIFICATION', None)
            with application_fixture() as app:
                try:
                    task, _, result = await self.run_doc(app)
                    self.assertEqual(result.execution_status, 'completed')
                    self.assertEqual(result.verification_status, 'verified')
                    self.assertEqual(result.changes, 'changed')
                    evidence = await app.sessions.list_verification_evidence(task.id)
                    self.assertTrue(any(row.provenance is EvidenceProvenance.SYSTEM_PLANNER
                                        for row in evidence))
                    self.assertFalse(any(row.provenance is EvidenceProvenance.SYSTEM_VERIFIER
                                         for row in evidence))
                finally:
                    await app.aclose()

    async def test_explicit_compatibility_disable_remains_unverified(self):
        with patch.dict(os.environ, {'CHAOS_STRUCTURED_VERIFICATION': '0'}):
            with application_fixture() as app:
                try:
                    _, _, result = await self.run_doc(app)
                    self.assertEqual(result.execution_status, 'completed')
                    self.assertEqual(result.verification_status, 'unverified')
                    self.assertEqual(result.exit_code(require_verified=True), 5)
                finally:
                    await app.aclose()

    async def test_explained_no_change_requires_explicit_user_partial_acceptance(self):
        with application_fixture() as app:
            try:
                task, _, result = await self.run_doc(app, write=False)
                self.assertEqual(result.execution_status, 'waiting_decision')
                self.assertEqual(result.changes, 'unchanged')
                self.assertNotEqual(result.verification_status, 'verified')
                reason = 'Reviewed the existing description; accept this no-change delivery.'
                accepted = await app.tasks.accept_partial(task.id, reason)
                self.assertEqual(accepted.status, TaskStatus.ACCEPTED_PARTIAL)
                self.assertEqual(accepted.stop_reason, reason)
                final = await app.tasks.result(task.id)
                self.assertEqual(final.execution_status, 'accepted_partial')
                self.assertEqual(final.changes, 'unchanged')
                self.assertEqual(final.verification_status, 'unverified')
                self.assertEqual(final.exit_code(require_verified=True), 5)
            finally:
                await app.aclose()
