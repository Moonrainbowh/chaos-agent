"""Read a real run's artifacts; no provider, fixture import, or code execution."""
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('run', type=Path)
args = parser.parse_args()
run = args.run.resolve()


def read(name):
    return json.loads((run / name).read_text(encoding='utf-8'))


result = read('worker-result.json')
durable = read('durable-' + result['parent_thread_id'] + '.json')
supervisor, freeze = read('real-supervisor.json'), read('freeze.json')
reviews = []
wire_integrity = True
settings_match = True
for wire in result['wire']:
    raw = (run / wire['body_file']).read_bytes()
    wire_integrity &= hashlib.sha256(raw).hexdigest() == wire['body_sha256']
    body = json.loads(raw)
    settings_match &= (body['model'], body['reasoning_effort'], body['max_completion_tokens']) == ('glm-5.3-flash', 'medium', 4096)
    try:
        payload = json.loads(body['messages'][-1]['content'])
    except (ValueError, KeyError, TypeError):
        continue
    if isinstance(payload, dict) and payload.get('phase') in ('independent', 'comparison'):
        reviews.append((body, payload))
initial = [p for _, p in reviews if p['phase'] == 'independent']
comparison = [p for _, p in reviews if p['phase'] == 'comparison']
usage = result['usage_records']
charged = sum(u['charged'] or 0 for u in usage)
events = [json.loads(line) for line in (run / 'real-events.jsonl').read_text(encoding='utf-8').splitlines() if line.startswith('{')]
elapsed = (datetime.fromisoformat(events[-1]['timestamp']) - datetime.fromisoformat(events[0]['timestamp'])).total_seconds()
last = durable['parent_review'][-1]
sources_unchanged = supervisor['after_hashes'] == freeze['workspace_hashes'] and all(
    hashlib.sha256((run / 'workspace' / name).read_bytes()).hexdigest() == value
    for name, value in freeze['workspace_hashes'].items())
summary = {
    'attempt': run.name, 'actual_exit': supervisor['actual_exit'],
    'candidate_unchanged': supervisor['candidate_unchanged'], 'sources_unchanged': sources_unchanged,
    'wire_integrity': wire_integrity, 'original_model_effort_output_budget': settings_match,
    'model_response_count': len(usage), 'usage_unique': len(usage) == len({u['id'] for u in usage}),
    'usage_statuses': [u['status'] for u in usage], 'charged_tokens': charged,
    'usage_matches_shared_budget': charged == result['parent_budget']['input_tokens'] + result['parent_budget']['output_tokens'],
    'child_tokens_already_included': sum(u['charged'] or 0 for u in result['children'][0]['usage']),
    'task_elapsed_seconds': round(elapsed, 3), 'parent_budget': result['parent_budget'],
    'parent_result': result['parent_result'], 'protocol_version': last['protocol_version'],
    'last_phase': last['phase'], 'repair_used': last['repair_used'],
    'review_attempts': [{'phase': a['phase'], 'errors': a['errors']} for a in durable['parent_review_attempts']],
    'independent_isolated': bool(initial) and all('child_advisory' not in p and 'child_objective' not in p and 'initial' not in p for p in initial),
    'comparison_full_advisory': bool(comparison) and all(p['child_advisory'] == result['children'][0]['messages'][-1]['content'] for p in comparison),
    'same_source_bundle': bool(reviews) and all(p['sources'] == reviews[0][1]['sources'] for _, p in reviews),
    'review_tool_free': all(not b.get('tools') for b, _ in reviews),
    'visible_equals_durable_rendered': last['phase'] == 'delivered' and result['parent_messages'][-1]['content'] == last['rendered'],
    'semantic_acceptance': 'REQUIRES_INDEPENDENT_REVIEW', 'audit_provider_calls': 0, 'fixture_execution': False,
}
(run / 'runtime-audit.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
