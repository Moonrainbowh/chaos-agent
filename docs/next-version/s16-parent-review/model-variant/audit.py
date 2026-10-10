"""Read-only runtime audit of one model variant; never imports its fixture."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('attempt', type=Path)
run = parser.parse_args().attempt.resolve()


def read(name):
    return json.loads((run / name).read_text(encoding='utf-8'))


result, supervisor, freeze = read('worker-result.json'), read('real-supervisor.json'), read('freeze.json')
routing = read('routing-observations.json')
parent = result['parent_thread_id']
durable = read('durable-' + parent + '.json')
last = durable['parent_review'][-1] if durable['parent_review'] else {}
children = result.get('children', [])
if not children:
    children = [read(path.name) for path in run.glob('durable-*.json')
                if path.name != 'durable-' + parent + '.json']
checks, reviews, responses = {}, [], []
checks['candidate_unchanged'] = supervisor['candidate_unchanged']
checks['sources_unchanged'] = supervisor['after_hashes'] == freeze['workspace_hashes'] and all(
    hashlib.sha256((run / 'workspace' / name).read_bytes()).hexdigest() == value
    for name, value in freeze['workspace_hashes'].items())
checks['wire_integrity'] = True
checks['routing_matches_wire'] = len(routing['wire_routes']) == len(result['wire'])
checks['frozen_effort_output'] = True
checks['correct_phase_model'] = True
for route, wire in zip(routing['wire_routes'], result['wire'], strict=True):
    raw = (run / wire['body_file']).read_bytes()
    body = json.loads(raw)
    digest = hashlib.sha256(raw).hexdigest()
    checks['wire_integrity'] &= digest == wire['body_sha256']
    checks['routing_matches_wire'] &= digest == route['body_sha256'] and route['thread_id'] == wire['thread_id']
    checks['frozen_effort_output'] &= body.get('reasoning_effort') == 'medium' and body.get('max_completion_tokens', body.get('max_tokens')) == 4096
    strong = route['phase'] in ('independent', 'comparison')
    checks['correct_phase_model'] &= body['model'] == ('global:gpt-6-astra' if strong else 'glm-5.3-flash')
    if strong:
        reviews.append((body, json.loads(body['messages'][-1]['content'])))
    response = {'request_model': body['model'], 'phase': route['phase'], 'thread': route['thread_id'],
                'http_status': route.get('http_status'), 'response_models': [], 'usage': None, 'finish_reasons': []}
    if route.get('response_file'):
        raw_events = (run / route['response_file']).read_text(encoding='utf-8')
        for line in raw_events.splitlines():
            if not line.startswith('data: ') or line == 'data: [DONE]':
                continue
            event = json.loads(line[6:])
            if event.get('model') and event['model'] not in response['response_models']:
                response['response_models'].append(event['model'])
            if event.get('usage'):
                response['usage'] = event['usage']
            for choice in event.get('choices', []):
                if choice.get('finish_reason'):
                    response['finish_reasons'].append(choice['finish_reason'])
    responses.append(response)
initial = [payload for _, payload in reviews if payload.get('phase') == 'independent']
comparison = [payload for _, payload in reviews if payload.get('phase') == 'comparison']
checks['review_tool_free'] = bool(reviews) and all(not b.get('tools') for b, _ in reviews)
checks['independent_isolated'] = bool(initial) and all(
    'child_advisory' not in p and 'child_objective' not in p and 'initial' not in p for p in initial)
checks['same_source_bundle'] = bool(reviews) and all(p['sources'] == reviews[0][1]['sources'] for _, p in reviews)
checks['comparison_full_advisory'] = bool(comparison) and all(
    p['child_advisory'] == children[0]['messages'][-1]['content'] for p in comparison)
checks['frozen_review_identity'] = last.get('review_model') == routing['identity']
checks['visible_equals_durable_rendered'] = last.get('phase') == 'delivered' and result.get('parent_messages', [{}])[-1]['content'] == last.get('rendered')
usages = result.get('usage_records', [])
checks['usage_unique'] = len(usages) == len({u['id'] for u in usages})
charged = sum(u.get('charged') or 0 for u in usages)
budget = result.get('parent_budget', {})
checks['usage_matches_shared_budget'] = charged == budget.get('input_tokens', -1) + budget.get('output_tokens', -1)
by_model = defaultdict(int)
for response in responses:
    usage = response.get('usage') or {}
    by_model[response['request_model']] += usage.get('prompt_tokens', 0) + usage.get('completion_tokens', 0)
checks['response_usage_matches_ledger'] = sum(by_model.values()) == charged
checks['all_usage_settled'] = bool(usages) and all(u['status'] == 'settled' for u in usages)
started = [e['payload']['model'] for e in durable['events'] if e['kind'] == 'model_started']
parent_wire = [w['model'] for w in result['wire'] if w['thread_id'] == parent]
checks['model_events_match_wire'] = started == parent_wire
summary = {'attempt': run.name, 'actual_exit': supervisor['actual_exit'],
    'runtime_checks': checks, 'runtime_checks_pass': all(checks.values()),
    'protocol_version': last.get('protocol_version'), 'last_phase': last.get('phase'),
    'repair_used': last.get('repair_used'), 'review_attempts': [
        {'phase': a['phase'], 'errors': a['errors']} for a in durable['parent_review_attempts']],
    'responses': responses, 'charged_tokens': charged, 'usage_by_requested_model': dict(by_model),
    'unsettled_reservations': [{'id': u['id'], 'status': u['status'], 'reserved': u['reserved']}
                             for u in usages if u['status'] != 'settled'],
    'charged_tokens_scope': 'known settled usage only; excludes any unknown final cost',
    'child_tokens_already_included': (sum(u['charged'] or 0 for u in children[0].get('usage', []))
                                    if result.get('children') else None),
    'elapsed_seconds': supervisor['elapsed_seconds'], 'parent_budget': budget,
    'parent_result': result.get('parent_result'), 'semantic_acceptance': 'REQUIRES_INDEPENDENT_REVIEW',
    'audit_provider_calls': 0, 'fixture_execution': False,
    'model_identity_scope': 'actual requested IDs and proxy response labels; backend implementation not independently attested'}
(run / 'runtime-audit.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
