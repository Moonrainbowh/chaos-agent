"""Independent saved-artifact audit; no Host/harness import or network."""
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess

folder = Path(__file__).resolve().parent
root = folder.parents[2]
owned = folder / 'owned-cases-p4/s16-source-completion-p4-72d041646622'
load = lambda path: json.loads(path.read_bytes())
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
freeze, result = load(owned / 'freeze.json'), load(owned / 'preflight.json')
fixture, supervisor = load(folder / 'source-fixture/fixture.json'), load(owned / 'preflight-supervisor.json')
head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
assert head == freeze['candidate_head'] == 'f8bb5dc75bd4808598e75100ee879248758ab9ce'
assert not subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=all',
    '--', 'src', 'chaos_agent', 'scripts', 'tests', 'pyproject.toml', 'uv.lock', 'AGENTS.md', 'AGENTS.*.md'])
for path, expected in freeze['harness_hashes'].items():
    assert sha(folder / path) == expected, path
for path, expected in freeze['settings_hashes'].items():
    assert sha(owned / path) == expected, path
for path, expected in freeze['workspace_hashes'].items():
    assert sha(owned / 'workspace' / path) == expected == supervisor['after_hashes'][path], path
original = Path('F:/code-ai-chaos/chaos-16-agent/docs/next-version/s16/owned-cases/next-version-s16-investigation-v7-09b160648af4')
assert sha(original / 'worker-result.json') == freeze['origin']['worker_result_sha256']
sources = set(fixture['required_source_paths'])
for path in sources:
    assert (original / 'workspace' / path).read_bytes() == (owned / 'workspace' / path).read_bytes() == base64.b64decode(fixture['source_bytes_base64'][path])
assert supervisor['actual_exit'] == 0 and supervisor['real_attempts'] == 0
assert result['status'] == 'OFFLINE_PUBLIC_PREFLIGHT_ONLY'
assert result['provider_calls'] == result['transport_attempts'] == 0
assert result['audit_entry_count'] == len(result['wire']) == 6
assert all(entry['stage'] == 'offline_response_only' and entry['external_send_attempt'] is None for entry in result['audit_entries'])
child, = result['children']
binding, = child['bindings']
assert set(binding['required_sources']) == sources and len(binding['required_sources']) == 4
assert binding['owner_thread_id'] == result['parent_thread_id']
assert binding['parent_task_id'] == result['parent_task_id']
assert binding['max_total_tokens'] == 300000 and binding['max_tool_calls'] == 5
assert child['relation']['parent_thread_id'] == result['parent_thread_id']
assert not child['source_correction_records']
bodies = []
for record in result['wire']:
    path = owned / record['body_file']
    assert sha(path) == record['body_sha256'] and path.stat().st_size == record['body_bytes']
    body = load(path)
    assert body['model'] == 'glm-5.3-flash' and body['reasoning_effort'] == 'medium'
    assert body['max_completion_tokens'] == 4096
    assert record['external_send_attempt'] is None
    bodies.append((record['thread_id'], body))
child_bodies = [body for thread, body in bodies if thread == child['thread_id']]
parent_bodies = [body for thread, body in bodies if thread == result['parent_thread_id']]
assert len(child_bodies) == 2 and len(parent_bodies) == 4
assert all(fixture['agent_instructions'] in '\n'.join(m['content'] for m in body['messages'] if m['role'] in {'system', 'developer'}) for body in child_bodies)
assert all('delegate once' not in json.dumps(body) and 'Check advisory independently' not in json.dumps(body) for body in child_bodies)
assert 'read' in {tool['function']['name'] for tool in child_bodies[0]['tools']}
schema = next(t['function']['parameters'] for t in parent_bodies[1]['tools'] if t['function']['name'] == 'delegate_agent')
assert schema['properties']['required_sources']['type'] == 'array' and 'required_sources' not in schema.get('required', [])
reads = {}
for message in child_bodies[1]['messages']:
    if message.get('role') == 'tool' and message.get('name') == 'read':
        value, end = json.JSONDecoder().raw_decode(message['content'])
        tail = message['content'][end:]
        assert not tail or re.fullmatch(r'\n\[history_ref window=[0-9a-f]{32} item=[0-9a-f]{32}\]', tail)
        assert value['is_error'] is False
        output = value['output']
        assert output['total_lines'] == len(output['text'].splitlines())
        reads[output['path']] = output['text']
assert set(reads) == sources
assert all(reads[path] == base64.b64decode(fixture['source_bytes_base64'][path]).decode('utf-8') for path in sources)
lease = result['initial_parent_budget']
assert (lease['lease_tier'], lease['lease_model_turn_limit'], lease['lease_tool_call_limit'], lease['lease_renewals']) == ('standard', 12, 30, 0)
assert lease['limits']['max_agent_rounds'] == 12 and lease['limits']['max_tool_calls'] == 40
assert lease['limits']['max_tool_calls_per_round'] == 8 and lease['limits']['max_total_tokens'] == 1000000
assert result['parent_task']['contract']['intent'] == 'analyze'
assert result['parent_result']['execution_status'] == 'completed' and result['parent_result']['verification_status'] == 'unverified'
assert result['parent_messages'][-1]['role'] == 'assistant' and result['parent_messages'][-1]['content'].strip()
advisory, = [json.loads(m['content'])['output'] for m in result['parent_messages'] if m.get('name') == 'delegate_agent' and m['role'] == 'tool']
assert advisory['status'] == 'completed' and advisory['usage']['tool_calls'] == 4
assert advisory['summary'].strip() and advisory['result']['verification_status'] == 'unknown'
old = load(folder / 'owned-cases-p4/s16-source-completion-p4-8bc3b259ade8/preflight-failure.json')
assert old['status'] == 'ATTEMPT_FAILED_PRESERVED' and old['error_type'] == 'KeyError'
assert old['provider_calls'] == 0 and len(old['wire']) == 6
assert not (owned / 'execution-started.json').exists()
print(json.dumps({'status': 'PASS_PREFLIGHT', 'candidate': head, 'parent_requests': 4, 'child_requests': 2,
    'source_bodies': 4, 'original_bytes_and_all_freezes_match': True, 'provider_calls': 0,
    'old_failure_preserved': True, 'quality_acceptance': 'NOT_TESTED_SYNTHETIC_RESPONSES'}, indent=2))
