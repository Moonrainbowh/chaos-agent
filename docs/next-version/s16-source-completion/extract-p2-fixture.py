"""Copy original v7 source bytes into a separately owned P2 input fixture."""
import hashlib
import base64
import json
from pathlib import Path

origin = Path('F:/code-ai-chaos/chaos-16-agent/docs/next-version/s16/owned-cases/next-version-s16-investigation-v7-09b160648af4')
target = Path(__file__).parent / 'source-fixture'
paths = ('docs/current-contract.md', 'docs/legacy-notes.md', 'names.py', 'test_names.py')
hashes = {}
encoded_sources = {}
for path in paths:
    source_bytes = (origin / 'workspace' / path).read_bytes()
    hashes[path] = hashlib.sha256(source_bytes).hexdigest()
    encoded_sources[path] = base64.b64encode(source_bytes).decode('ascii')
    assert base64.b64decode(encoded_sources[path], validate=True) == source_bytes
child = ('Read docs/current-contract.md, docs/legacy-notes.md, names.py and test_names.py in full. '
    'Use available read schema. Compare current/legacy constraints, code/tests; '
    'cite physical 1-based path:line counting blanks. No edits, commands/tests, outside sources '
    'or delegation; unverified, tests unexecuted.')
parent = ('Read-only. Load delegate_agent if absent; next model request delegate once to '
    's16.sourceaudit (300000 tokens,5 tools,240s). Check advisory against sources; '
    'no notes/new_context. Child: ' + child)
assert len(parent) <= 512, len(parent)
fixture = {'version': 's16-source-fixture-p2', 'origin': origin.name, 'status': 'OFFLINE_FIXTURE_ONLY',
    'parent_input': parent, 'child_objective': child,
    'agent_instructions': 'Read the authorized sources only. Return source-grounded advisory analysis. '
        'Distinguish current constraints from superseded references. Do not edit, execute, '
        'access outside files or delegate.',
    'agent_id': 's16.sourceaudit', 'required_source_paths': list(paths), 'source_sha256': hashes,
    'source_bytes_base64': encoded_sources,
    'runtime': {'model': 'glm-5.3-flash', 'reasoning_effort': 'medium', 'parent_token_budget': 1000000,
        'parent_base_mode': 'medium', 'child_base_mode': 'medium',
        'child_token_budget': 300000, 'host_prompt_tokens': 300000, 'tool_schema_tokens': 20000,
        'max_output_tokens': 4096, 'max_agent_rounds': 12, 'max_tool_calls': 40,
        'max_tool_calls_per_round': 8, 'child_tool_budget': 5, 'child_active_seconds': 240,
        'parent_watchdog_seconds': 880, 'total_seconds': 900, 'transport_timeout_seconds': 60,
        'transport_max_retries': 2}}
(target / 'fixture.json').write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'copied_source_bytes_verified': True, 'source_sha256': hashes}, indent=2))
