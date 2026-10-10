"""Read saved artifacts only; never import fixture code or call a Provider."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ATTEMPTS = HERE.parent / 'fast-acceptance/attempts'
RUN = ATTEMPTS / 's16-source-completion-p4-75e2fe91ce07'
INFRA = ATTEMPTS / 's16-source-completion-p4-a29c2c8b16f8'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(data):
    return hashlib.sha256(data).hexdigest()


result = read(RUN / 'worker-result.json')
durable = read(RUN / ('durable-' + result['parent_thread_id'] + '.json'))
supervisor = read(RUN / 'real-supervisor.json')
freeze = read(RUN / 'freeze.json')
for path, expected in freeze['workspace_hashes'].items():
    assert digest((RUN / 'workspace' / path).read_bytes()) == expected
assert supervisor['after_hashes'] == freeze['workspace_hashes']
assert supervisor['candidate_unchanged']
assert read(RUN / 'candidate-manifest.json') == read(INFRA / 'candidate-manifest.json')

for entry in result['wire']:
    body = (RUN / entry['body_file']).read_bytes()
    assert digest(body) == entry['body_sha256']
    payload = json.loads(body)
    assert payload['model'] == 'glm-5.3-flash'
    assert payload['reasoning_effort'] == 'medium'
    assert payload['max_completion_tokens'] == 4096

bodies = [read(RUN / ('real-wire-request-' + str(i) + '.json')) for i in (6, 7, 8)]
phases = [json.loads(body['messages'][-1]['content']) for body in bodies]
assert [p['phase'] for p in phases] == ['independent', 'comparison', 'comparison']
assert all([m['role'] for m in body['messages']] == ['system', 'user'] for body in bodies)
assert all(not body.get('tools') for body in bodies)
assert not {'initial', 'child_advisory', 'child_objective', 'advisory_paragraphs', 'repair'} & phases[0].keys()
assert all(p['sources'] == phases[0]['sources'] for p in phases)
for source in phases[0]['sources']:
    assert digest(source['text'].encode()) == freeze['source_sha256'][source['path']]
    assert source['physical_lines'] == [
        {'line': i, 'text': line} for i, line in enumerate(source['text'].splitlines(keepends=True), 1)
    ]

attempts = durable['parent_review_attempts']
assert len(attempts) == 3 and not attempts[0]['errors']
assert phases[2]['repair']['previous_response'] == attempts[1]['raw_output']
assert phases[2]['repair']['errors'] == attempts[1]['errors']
assert attempts[1]['errors'] == [{'path': '/comparisons/-', 'message': 'append comparison for uncovered paragraphs: [5, 9, 12]'}]
assert attempts[2]['errors'] == [{'path': '', 'message': 'repair must contain only 1..32 replacements for the reported paths'}]
last_raw = json.loads(attempts[2]['raw_output'])
assert 'findings' in last_raw and 'replacements' not in last_raw
missing = [p for p in phases[1]['advisory_paragraphs'] if p['id'] in (5, 9, 12)]
assert all(p['text'].startswith('### ') and '\n' not in p['text'] for p in missing)
assert not any(p['phase'] == 'delivered' for p in durable['parent_review'])
assert result['parent_result']['execution_status'] == 'failed'
assert result['parent_result']['verification_status'] == 'unverified'

usage = result['usage_records']
assert len(usage) == len({u['id'] for u in usage}) == 8
assert all(u['status'] == 'settled' and u['budget_owner_thread_id'] == result['parent_thread_id'] for u in usage)
tokens_in = sum(u['usage']['input_tokens'] for u in usage)
tokens_out = sum(u['usage']['output_tokens'] for u in usage)
assert sum(u['charged'] for u in usage) == tokens_in + tokens_out == 43776
assert tokens_in == result['parent_budget']['input_tokens']
assert tokens_out == result['parent_budget']['output_tokens']
child = result['children'][0]
assert len(result['children']) == 1 and len(result['child_entry_observations']) == 1
assert {u['id'] for u in child['usage']} <= {u['id'] for u in usage}
assert phases[1]['child_advisory'] == child['messages'][-1]['content']
stdout_lines = (RUN / 'real-events.jsonl').read_text(encoding='utf-8').splitlines()
# The unchanged Host also prints terminal child-progress lines on stdout.
events = [json.loads(line) for line in stdout_lines if line.startswith('{')]
elapsed = (datetime.fromisoformat(events[-1]['timestamp']) - datetime.fromisoformat(events[0]['timestamp'])).total_seconds()
infra = read(INFRA / 'worker-result.json')
assert infra['usage_records'][0]['status'] == 'pending'

summary = {
    'status': 'S16_NOT_ACCEPTED', 'attempt': RUN.name,
    'artifact_audit': 'PASS', 'model': 'glm-5.3-flash', 'reasoning_effort': 'medium',
    'actual_model_responses': 8, 'transport_attempts': 8,
    'input_tokens': tokens_in, 'output_tokens': tokens_out, 'settled_tokens': tokens_in + tokens_out,
    'child_tokens_included_once': sum(u['charged'] for u in child['usage']),
    'task_elapsed_seconds': round(elapsed, 3), 'budget_active_seconds': result['parent_budget']['active_seconds'],
    'stage_attempts': [{'phase': a['phase'], 'errors': a['errors']} for a in attempts],
    'missing_paragraphs_are_headings': missing,
    'final_rejection_raw_preserved': True, 'sources_and_candidate_unchanged': True,
    'parent_result': result['parent_result'],
    'infrastructure_attempt': {'attempt': INFRA.name, 'transport_attempts': 3, 'model_replies_observed': 0,
                               'usage_status': 'pending', 'reserved_tokens': 40615, 'settled_tokens': None},
    'audit_provider_calls': 0, 'fixture_execution': False,
    'non_json_stdout_lines_preserved': sum(bool(line.strip()) and not line.startswith('{') for line in stdout_lines),
}
(HERE / 'real-run-audit.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
