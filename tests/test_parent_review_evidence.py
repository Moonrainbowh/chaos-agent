"""Host protocol facts through the actual production source-delegation route."""
import unittest
import json
from unittest.mock import AsyncMock, patch
from code_agent.core.cancellation import CancellationToken
from code_agent.core.parent_review import ParentReviewSnapshot
from tests import test_parent_source_review as support


class ParentReviewEvidenceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.ParentSourceReviewTests.asyncSetUp
    asyncTearDown = support.ParentSourceReviewTests.asyncTearDown
    provider = support.ParentSourceReviewTests.provider
    run_parent = support.ParentSourceReviewTests.run_parent
    source_run = support.ParentSourceReviewTests.source_run
    delivery = support.ParentSourceReviewTests.delivery

    async def test_host_v2_facts_and_requirement_evidence_are_frozen(self):
        advisory = 'Child full advisory.'
        task, _, _, _, _ = await self.source_run(
            [[support.answer(self.delivery())], [support.answer(self.delivery(advisory))]],
            advisory=advisory)
        records = await self.app.sessions.context_records(task.thread_id, 'parent_review')
        initial = records[0]
        self.assertEqual(initial['protocol_version'], 4)
        kinds = {r['id']: r['evidence_kind'] for r in initial['requirements']}
        self.assertEqual({k for k, v in kinds.items() if v == 'source_or_runtime'},
                         {'task_objective', 'child_objective'})
        self.assertTrue(all(kinds[k] == 'source' for k in
                             ('implementation', 'contract_compliance', 'test_discrimination')))
        facts = {r['id']: r for r in initial['runtime_evidence']}
        self.assertEqual(set(facts), {'parent_sources_frozen', 'parent_review_tool_free', 'child_lifecycle_status'})
        self.assertEqual(facts['child_lifecycle_status']['phase'], 'comparison')
        self.assertIn('completed', facts['child_lifecycle_status']['description'])
        self.assertIn('does not prove', facts['child_lifecycle_status']['description'])
        self.assertEqual(facts['parent_sources_frozen']['phase'], 'independent')
        self.assertEqual(facts['parent_review_tool_free']['phase'], 'independent')
        self.assertTrue(initial['sources'][0]['version'])
        self.assertEqual(initial['sources'][0]['provenance'], 'host_authorized_full_read')
        for record in records:
            self.assertEqual(record['runtime_evidence'], initial['runtime_evidence'])


    async def test_capacity_block_keeps_full_attempt_and_consumed_repair(self):
        adapter = self.app.controller._engine._parent_review
        task = await self.app.tasks.start('Read only: inspect sources without commands.')
        snapshot = await adapter.record(task, ParentReviewSnapshot(),
            {'task_id': task.id, 'phase': 'comparison', 'repair_used': False})
        raw = 'full rejected output ' * 10000
        attempt = {'task_id': task.id, 'phase': 'comparison', 'raw_output': raw,
                   'errors': [{'path': '/findings/0/citations', 'message': 'missing citation'}]}
        result = await adapter.record(task, snapshot, {'attempt': attempt,
                                      'repair_used': True, 'repair_response': raw})
        self.assertEqual(result.phase, 'blocked')
        self.assertTrue(result.data['repair_used'])
        attempts = await self.app.sessions.context_records(task.thread_id, 'parent_review_attempt')
        self.assertEqual(attempts[-1]['raw_output'], raw)
        self.assertEqual(attempts[-1]['errors'], attempt['errors'])
        records = await self.app.sessions.context_records(task.thread_id, 'parent_review')
        self.assertTrue(records[-1]['repair_used'])
        self.assertNotIn('attempt', records[-1])
        self.assertNotIn('repair_response', records[-1])
        self.assertFalse(any(m.role == 'assistant' for m in await self.app.sessions.load_messages(task.thread_id)))

    async def test_near_capacity_snapshot_blocks_without_losing_attempt(self):
        adapter = self.app.controller._engine._parent_review
        task = await self.app.tasks.start('Read only: inspect source evidence.')
        data = {'task_id': task.id, 'delegate_id': 'bound-delegate', 'protocol_version': 2,
                'phase': 'comparison', 'repair_used': False, 'user_cursor': 0,
                'source_errors': [], 'padding': ''}
        data['padding'] = 'x' * (131060 - len(json.dumps(data, ensure_ascii=False).encode()))
        self.assertEqual(len(json.dumps(data, ensure_ascii=False).encode()), 131060)
        snapshot = await adapter.record(task, ParentReviewSnapshot(), data)
        attempt = {'task_id': task.id, 'phase': 'comparison', 'raw_output': 'RAW_AUDIT_MARKER' * 100,
                   'errors': [{'path': '/findings', 'message': 'missing coverage'}]}
        blocked = await adapter.record(task, snapshot, {'attempt': attempt, 'repair_used': True,
                                      'repair_response': attempt['raw_output']})
        self.assertEqual(blocked.phase, 'blocked')
        self.assertTrue(blocked.data['repair_used'])
        self.assertEqual(blocked.data['blocked_from_id'], snapshot.data['id'])
        self.assertNotIn('padding', blocked.data)
        attempts = await self.app.sessions.context_records(task.thread_id, 'parent_review_attempt')
        self.assertEqual(attempts[-1]['raw_output'], attempt['raw_output'])
        self.assertEqual(attempts[-1]['errors'], attempt['errors'])
        records = await self.app.sessions.context_records(task.thread_id, 'parent_review')
        self.assertEqual(records[0]['padding'], data['padding'])
        self.assertEqual(len(records), 2)
        with patch.object(adapter.sessions, 'history_stats', AsyncMock(side_effect=AssertionError('blocked must not prepare'))):
            resumed = await adapter.prepare(task, CancellationToken())
        self.assertEqual(resumed.data, blocked.data)
