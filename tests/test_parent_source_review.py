"""Parent review regressions at the real task/provider boundary."""
import json
import hashlib
import unittest
from dataclasses import replace
from unittest.mock import patch
from code_agent.core.limits import BudgetReservation, BudgetReserveStatus
from tests import test_s16_source_completion as support

call, answer = support.call, support.answer


class ParentSourceReviewTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.S16ProductionRegressionTests.asyncSetUp
    asyncTearDown = support.S16ProductionRegressionTests.asyncTearDown
    provider = support.S16ProductionRegressionTests.provider
    run_parent = support.S16ProductionRegressionTests.run_parent

    def delivery(self, advisory=None):
        text = (self.root / 'names.py').read_bytes().decode('utf-8')
        citation = {'path': 'names.py', 'version': hashlib.sha256(text.encode()).hexdigest(),
                    'start_line': 1, 'end_line': 1, 'quote': text.splitlines(keepends=True)[0]}
        output = {'findings': [{'requirement_id': k, 'judgment': 'Source finding: ' + k,
            'citations': [citation]} for k in ('implementation', 'contract_compliance', 'test_discrimination', 'task_objective', 'child_objective')],
            'assertion_checks': [], 'unknowns': ['This structural fixture has no test assertion source.']}
        if not advisory:
            output['findings'] = [f for f in output['findings'] if f['requirement_id'] != 'child_objective']
        if advisory:
            output['comparisons'] = [{'child_claim': 'Advisory assessed.', 'paragraph_ids': list(range(1, len(advisory.split('\n\n')) + 1)),
                'assessment': 'Retained with qualification', 'rationale': 'Static source reasoning.',
                'citations': [citation]}]
            output = {'confirmed_findings': [f['requirement_id'] for f in output['findings']
                                            if f['requirement_id'] != 'child_objective'],
                      'finding_updates': [f for f in output['findings'] if f['requirement_id'] == 'child_objective'],
                      'confirmed_assertions': [], 'assertion_updates': [],
                      'unknowns': output['unknowns'], 'comparisons': output['comparisons']}
        return json.dumps(output)

    async def source_run(self, review_streams, *, advisory='Child full advisory.', mutate=None, child_objective='Analyze sources.'):
        if mutate:
            mutate()
        streams = [[call('source-delegate', 'delegate_agent', {
            'agent_id': 's16.sourceaudit', 'objective': child_objective,
            'required_sources': ['names.py'], 'token_budget': 60000})]] + review_streams
        child = [[call('read-source', 'read', {'operation': 'file', 'path': 'names.py'})],
                 [answer(advisory)]]
        return await self.run_parent('Read only: deep source investigation. Delegate once and review '
            'implementation, contract compliance, test discrimination and unknowns.', streams, (child,))

    async def test_valid_delivery_is_rendered_unverified_and_uses_original_budget(self):
        advisory = 'ADVISORY_KEEP_ME'
        task, events, parent, _, _ = await self.source_run(
            [[answer(self.delivery())], [answer(self.delivery(advisory))]], advisory=advisory)
        self.assertEqual(task.status.value, 'completed', [e.to_dict() for e in events])
        self.assertEqual(len(parent.bodies), 4)
        records = await self.app.sessions.context_records(task.thread_id, 'parent_review')
        self.assertEqual(records[0]['protocol_version'], 4)
        self.assertIn('confirmed_findings', parent.bodies[3]['instructions'])
        attempts = await self.app.sessions.context_records(task.thread_id, 'parent_review_attempt')
        self.assertIn('confirmed_findings', json.loads(attempts[-1]['raw_output']))
        self.assertEqual(len(json.loads(attempts[-1]['effective_output'])['findings']), 5)
        self.assertIn('Simplified Chinese', parent.bodies[2]['instructions'])
        self.assertIn('assertion_checks', parent.bodies[2]['instructions'])
        delegate = next(t.get('function', t) for t in parent.bodies[1]['tools']
                        if t.get('function', t)['name'] == 'delegate_agent')
        for description in (delegate['description'],
                delegate['parameters']['properties']['role']['description'],
                delegate['parameters']['properties']['agent_id']['description']):
            self.assertIn('exactly one of role or agent_id', description)
        self.assertIn('omit role', delegate['description'])
        messages = await self.app.sessions.load_messages(task.thread_id)
        self.assertIn('本次仅静态分析', messages[-1].content)
        self.assertNotIn('findings', messages[-1].content)
        result = [e.payload['result'] for e in events if 'result' in e.payload][-1]
        self.assertEqual(result['verification_status'], 'unverified')
        budget = await self.app.sessions.load_task_budget(task.id)
        self.assertEqual(budget.model_turns, 6)  # parent 4 + existing child 2
        self.assertEqual(budget.tool_calls, 4)  # disclosure + delegate + child read + Host read
        self.assertEqual(len(self.app.subagents._child_threads), 1)
        visible = [e.to_dict() for e in events if e.kind.value in ('model_event', 'message_added')]
        self.assertNotIn('"findings"', json.dumps(visible, ensure_ascii=False))
        durable = await self.app.sessions.load_events(task.thread_id)
        self.assertNotIn('\\"findings\\"', json.dumps([e.to_dict() for e in durable], ensure_ascii=False))
        self.assertFalse(any(m.role == 'assistant' and m.content.startswith('{') for m in messages))

    async def test_only_one_repair_and_invalid_citations_fail_delivery(self):
        valid = json.loads(self.delivery('Child full advisory.'))
        valid['finding_updates'][0]['citations'][0]['end_line'] = 99999
        invalid = json.dumps(valid)
        task, events, parent, _, _ = await self.source_run(
            [[answer(self.delivery())], [answer(invalid)], [answer(invalid)]])
        self.assertEqual(len(parent.bodies), 5)
        self.assertEqual(task.status.value, 'failed')
        result = [e.payload['result'] for e in events if 'result' in e.payload][-1]
        self.assertEqual(result['stop_code'], 'parent_review_delivery_unmet')
        self.assertTrue(result['remaining'])
        attempts = await self.app.sessions.context_records(task.thread_id, 'parent_review_attempt')
        self.assertEqual(len(attempts), 3)
        self.assertEqual(attempts[-1]['raw_output'], invalid)
        self.assertTrue(attempts[-1]['errors'])
        self.assertEqual(attempts[-1]['phase'], 'comparison')

    async def test_complete_advisory_survives_distilled_tool_limit(self):
        advisory = 'Long advisory prefix. ' * 140 + '\n\nTAIL_ADVISORY_ASSERTION_MARKER'
        task, _, parent, _, _ = await self.source_run(
            [[answer(self.delivery())], [answer(self.delivery(advisory))]], advisory=advisory)
        self.assertEqual(task.status.value, 'completed')
        self.assertNotIn('TAIL_ADVISORY_ASSERTION_MARKER', json.dumps(parent.bodies[2]))
        self.assertIn('TAIL_ADVISORY_ASSERTION_MARKER', json.dumps(parent.bodies[3]))

    async def test_initial_repair_remains_isolated_then_comparison_delivers(self):
        task, _, parent, _, _ = await self.source_run([[answer('Only a plan.')],
            [answer(self.delivery())], [answer(self.delivery('Child full advisory.'))]])
        self.assertEqual(task.status.value, 'completed')
        self.assertEqual(len(parent.bodies), 5)
        self.assertNotIn('Child full advisory.', json.dumps(parent.bodies[2:4]))
        self.assertIn('Child full advisory.', json.dumps(parent.bodies[4]))
        self.assertIn('Only a plan.', json.dumps(parent.bodies[3]))
        self.assertIn('previous_response', json.dumps(parent.bodies[3]))
        self.assertIn('Return one JSON object: {"findings"', parent.bodies[3]['instructions'])
        self.assertNotIn('"replacements"', parent.bodies[3]['instructions'])

    async def test_heading_groups_deliver_without_spending_repair(self):
        advisory = '# Report\n\nFirst assertion.\n\n## More claims\n\nSecond assertion.'
        final = json.loads(self.delivery(advisory))
        final['comparisons'][0]['paragraph_ids'] = [2, 4]
        task, events, parent, _, _ = await self.source_run(
            [[answer(self.delivery())], [answer(json.dumps(final))]], advisory=advisory)
        self.assertEqual(task.status.value, 'completed')
        self.assertEqual(len(parent.bodies), 4)
        request = json.loads(parent.bodies[3]['input'][-1]['content'])
        self.assertEqual(request['child_advisory'], advisory)
        self.assertEqual(request['advisory_paragraphs'], [
            {'id': 2, 'text': '# Report\n\nFirst assertion.'},
            {'id': 4, 'text': '## More claims\n\nSecond assertion.'}])
        records = await self.app.sessions.context_records(task.thread_id, 'parent_review')
        self.assertEqual(records[-1]['phase'], 'delivered')
        self.assertFalse(records[-1]['repair_used'])
        attempts = await self.app.sessions.context_records(task.thread_id, 'parent_review_attempt')
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(not attempt['errors'] for attempt in attempts))
        result = [e.payload['result'] for e in events if 'result' in e.payload][-1]
        self.assertEqual(result['verification_status'], 'unverified')

    async def test_field_repair_preserves_valid_claims_and_binds_host_citations(self):
        initial = json.loads(self.delivery())
        for finding in initial['findings']:
            finding['citations'][0].pop('version')
            finding['citations'][0].pop('quote')
        initial['findings'][0]['citations'][0]['end_line'] = 999
        raw = json.dumps(initial)
        correction = json.dumps({'replacements': [
            {'path': '/findings/0/citations/0/end_line', 'value': 1}]})
        task, events, parent, _, _ = await self.source_run([[answer(raw)],
            [answer(correction)], [answer(self.delivery('Child full advisory.'))]])
        self.assertEqual(task.status.value, 'completed')
        self.assertEqual(len(parent.bodies), 5)
        self.assertIn('previous_response', json.dumps(parent.bodies[3]))
        self.assertIn('/findings/0/citations/0/end_line', json.dumps(parent.bodies[3]))
        repair_system = parent.bodies[3]['instructions']
        self.assertIn('"replacements"', repair_system)
        self.assertNotIn('Return one JSON object: {"findings"', repair_system)
        attempts = await self.app.sessions.context_records(task.thread_id, 'parent_review_attempt')
        self.assertEqual([r['phase'] for r in attempts], ['independent', 'independent', 'comparison'])
        self.assertEqual(attempts[0]['raw_output'], raw)
        self.assertEqual(attempts[1]['raw_output'], correction)
        effective = json.loads(attempts[1]['effective_output'])
        self.assertEqual(effective['findings'][1:], initial['findings'][1:])
        self.assertFalse(attempts[1]['errors'])
        records = await self.app.sessions.context_records(task.thread_id, 'parent_review')
        frozen_initial = json.loads(records[-1]['initial'])
        self.assertTrue(frozen_initial['findings'][0]['citations'][0]['version'])
        self.assertEqual(frozen_initial['findings'][0]['citations'][0]['quote'],
                         (self.root / 'names.py').read_bytes().decode('utf-8').splitlines(keepends=True)[0])
        result = [e.payload['result'] for e in events if 'result' in e.payload][-1]
        self.assertEqual(result['verification_status'], 'unverified')
        self.assertEqual((await self.app.sessions.load_task_budget(task.id)).model_turns, 7)

    async def test_model_authored_child_objective_is_excluded_from_independent_wire(self):
        marker = 'OLD_NOTES_ADVISORY_IN_CHILD_OBJECTIVE'
        task, _, parent, _, _ = await self.source_run(
            [[answer(self.delivery())], [answer(self.delivery('Child full advisory.'))]],
            child_objective='Analyze sources. ' + marker)
        self.assertEqual(task.status.value, 'completed')
        self.assertNotIn(marker, json.dumps(parent.bodies[2]))
        self.assertIn(marker, json.dumps(parent.bodies[3]))

    async def test_hard_budget_after_child_has_no_host_read_or_parent_model(self):
        engine = self.app.controller._engine
        engine._limits = replace(engine._limits, max_agent_rounds=4)
        task, events, parent, _, _ = await self.source_run([])
        self.assertEqual(len(parent.bodies), 2)
        self.assertEqual(task.status.value, 'paused')
        budget = await self.app.sessions.load_task_budget(task.id)
        self.assertEqual(budget.tool_calls, 3)
        result = [e.payload['result'] for e in events if 'result' in e.payload][-1]
        self.assertEqual(result['stop_code'], 'parent_review_budget_exhausted')
        self.assertTrue(result['remaining'])

    async def test_soft_lease_shared_completion_gate_rejects_unreviewed_delivery(self):
        review = self.app.controller._engine._parent_review
        original_prepare, original_reserve = review.prepare, self.app.sessions.reserve_task_budget
        activated = False
        async def prepare(task, token):
            nonlocal activated
            snapshot = await original_prepare(task, token)
            activated = snapshot.active
            return snapshot
        async def reserve(thread, **kwargs):
            if activated and kwargs.get('model_turns'):
                budget = await self.app.sessions.get_or_create_task_budget(thread, 'test', self.app.controller._engine._limits)
                return BudgetReservation(budget, BudgetReserveStatus.LEASE_EXHAUSTED, 'scripted soft boundary')
            return await original_reserve(thread, **kwargs)
        with patch.object(review, 'prepare', prepare), patch.object(self.app.sessions, 'reserve_task_budget', reserve):
            task, events, parent, _, _ = await self.source_run([])
        self.assertEqual(len(parent.bodies), 2)
        self.assertEqual(task.status.value, 'failed')
        result = [e.payload['result'] for e in events if 'result' in e.payload][-1]
        self.assertEqual(result['stop_code'], 'parent_review_delivery_unmet')

    async def test_source_disappears_after_child_blocks_parent_provider(self):
        review = self.app.controller._engine._parent_review
        original = review.prepare
        async def prepare(task, token):
            if self.app.subagents._child_threads and (self.root / 'names.py').exists():
                (self.root / 'names.py').unlink()
            return await original(task, token)
        with patch.object(review, 'prepare', prepare):
            task, events, parent, _, _ = await self.source_run([])
        self.assertEqual(len(parent.bodies), 2)
        self.assertEqual(task.status.value, 'failed')
        self.assertIn('names.py', task.stop_reason)


    async def test_source_delegate_enters_two_isolated_parent_requests(self):
        report = 'CHILD_ADVISORY_CONTAMINATION_MARKER'
        streams = [[call('source-delegate', 'delegate_agent', {
            'agent_id': 's16.sourceaudit', 'objective': 'Analyze sources.',
            'required_sources': ['names.py'], 'token_budget': 60000})],
            [answer('Independent judgment.')], [answer('Comparison.')],
            [answer('Still incomplete.')]]
        child = [[call('read-source', 'read', {'operation': 'file', 'path': 'names.py'})],
                 [answer(report)]]
        task, events, parent, _, results = await self.run_parent(
            'Read only: deep source investigation. Delegate once and review implementation, '
            'contract compliance, test discrimination and unknowns.', streams, (child,))
        self.assertGreaterEqual(len(parent.bodies), 4,
            'RED: only one parent answer request; independent and comparison stages absent')
        first, second = parent.bodies[2:4]
        self.assertNotIn(report, json.dumps(first))
        self.assertNotIn(report, json.dumps(second))  # one isolated repair of the incomplete first stage
        self.assertIn('physical_lines', json.dumps(first))
        self.assertEqual(first.get('tools', []), [])
        self.assertEqual(second.get('tools', []), [])
        self.assertNotEqual(task.status.value, 'completed')
