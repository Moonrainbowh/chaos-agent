"""Extract immutable v7 prompt/events/sources for portable offline regressions."""
import hashlib
import json
from pathlib import Path

origin = Path('F:/code-ai-chaos/chaos-16-agent/docs/next-version/s16/owned-cases/next-version-s16-investigation-v7-09b160648af4')
root = Path(__file__).resolve().parents[3]
child_path = origin / 'child-f828d878b1274052a92d5a7467751d77.json'
events = json.loads(child_path.read_text(encoding='utf-8'))
turns = []
for event in events:
    if event['kind'] == 'model_started':
        turns.append([])
    elif event['kind'] == 'model_event':
        turns[-1].append(event['payload']['event'])
paths = ('docs/current-contract.md', 'docs/legacy-notes.md', 'names.py', 'test_names.py')
manifest = json.loads((origin / 'state/chaos-agent/plugins/s16-acceptance/plugin.json').read_text(encoding='utf-8'))
value = {
    'origin': origin.name,
    'original_child_event_sha256': hashlib.sha256(child_path.read_bytes()).hexdigest(),
    'parent_prompt': json.loads((origin / 'worker-result.json').read_text(encoding='utf-8'))['parent_task']['contract']['objective'],
    'child_objective': next(e['payload']['message']['content'] for e in events
        if e['kind'] == 'message_added' and e['payload']['message']['role'] == 'user'),
    'agent_instructions': manifest['contributions']['agents'][0]['instructions'],
    'child_turns': turns,
    'sources': {path: (origin / 'workspace' / path).read_text(encoding='utf-8') for path in paths},
    'source_sha256': {path: hashlib.sha256((origin / 'workspace' / path).read_bytes()).hexdigest() for path in paths},
}
target = root / 'tests/fixtures/s16-v7.json'
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(target)
