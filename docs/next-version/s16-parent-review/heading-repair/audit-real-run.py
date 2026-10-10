"""Audit recorded runtime evidence without executing fixture code or a model."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = HERE.parent / 'fast-acceptance/attempts/s16-source-completion-p4-74e53e65f9c7'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


result = read(RUN / 'worker-result.json')
durable = read(RUN / ('durable-' + result['parent_thread_id'] + '.json'))
supervisor = read(RUN / 'real-supervisor.json')
freeze = read(RUN / 'freeze.json')
assert supervisor['actual_exit'] == 0 and supervisor['candidate_unchanged']
for path, value in freeze['workspace_hashes'].items():
    assert hashlib.sha256((RUN / 'workspace' / path).read_bytes()).hexdigest() == value
assert supervisor['after_hashes'] == freeze['workspace_hashes']
for wire in result['wire']:
    raw = (RUN / wire['body_file']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == wire['body_sha256']
    body = json.loads(raw)
    assert (body['model'], body['reasoning_effort'], body['max_completion_tokens']) == ('glm-5.3-flash', 'medium', 4096)
review_bodies = [read(RUN / f'real-wire-request-{i}.json') for i in (5, 6)]
review_inputs = [json.loads(b['messages'][-1]['content']) for b in review_bodies]
assert [b['phase'] for b in review_inputs] == ['independent', 'comparison']
assert all(not b.get('tools') for b in review_bodies)
assert 'child_advisory' not in review_inputs[0] and 'child_objective' not in review_inputs[0]
assert review_inputs[0]['sources'] == review_inputs[1]['sources']
assert review_inputs[1]['child_advisory'] == result['children'][0]['messages'][-1]['content']
assert len(durable['parent_review_attempts']) == 2
assert all(not a['errors'] for a in durable['parent_review_attempts'])
assert durable['parent_review'][-1]['phase'] == 'delivered'
assert not durable['parent_review'][-1]['repair_used']
assert result['parent_messages'][-1]['role'] == 'assistant'
assert result['parent_messages'][-1]['content'] == durable['parent_review'][-1]['rendered']
usage = result['usage_records']
assert len(usage) == len({u['id'] for u in usage}) == 6
assert all(u['status'] == 'settled' for u in usage)
charged = sum(u['charged'] for u in usage)
assert charged == sum(u['usage']['input_tokens'] + u['usage']['output_tokens'] for u in usage) == 32008
assert charged == result['parent_budget']['input_tokens'] + result['parent_budget']['output_tokens']
events = [json.loads(line) for line in (RUN / 'real-events.jsonl').read_text(encoding='utf-8').splitlines() if line.startswith('{')]
elapsed = (datetime.fromisoformat(events[-1]['timestamp']) - datetime.fromisoformat(events[0]['timestamp'])).total_seconds()
summary = {'artifact_audit': 'PASS', 'delivery': 'PASS', 'semantic_acceptance': 'NOT_ACCEPTED',
    'attempt': RUN.name, 'provider_responses': 6, 'settled_tokens': charged,
    'input_tokens': result['parent_budget']['input_tokens'], 'output_tokens': result['parent_budget']['output_tokens'],
    'child_tokens_included_once': sum(u['charged'] for u in result['children'][0]['usage']),
    'task_elapsed_seconds': round(elapsed, 3), 'review_repairs': 0,
    'parent_result': result['parent_result'], 'candidate_and_sources_unchanged': True,
    'audit_provider_calls': 0, 'fixture_execution': False}
(HERE / 'real-run-audit.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
