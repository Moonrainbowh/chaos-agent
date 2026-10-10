"""Offline replay of the preserved real rejection. No Provider or fixture execution."""
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'src'))
from code_agent.core.parent_review import ParentReviewSnapshot, evaluate_response

owned = ROOT / 'docs/next-version/s16-parent-review/fast-acceptance/attempts/s16-source-completion-p4-beab64fc437a'
run = json.loads((owned / 'worker-result.json').read_text('utf-8'))
durable = json.loads((owned / ('durable-' + run['parent_thread_id'] + '.json')).read_text('utf-8'))
snapshot = next(r for r in durable['parent_review'] if r.get('rejected_initial'))
raw = snapshot['rejected_initial']
baseline = copy.deepcopy(snapshot)
baseline.update(repair=[], repair_used=False)
_, before_errors, _ = evaluate_response(raw, ParentReviewSnapshot(baseline), comparison=False)
assert before_errors and any('/findings/3/citations' == e['path'] for e in before_errors)
candidate = copy.deepcopy(baseline)
candidate['protocol_version'] = 2
for requirement in candidate['requirements']:
    requirement['evidence_kind'] = 'source_or_runtime' if requirement['id'] in ('task_objective', 'child_objective') else 'source'
candidate['runtime_evidence'] = [{'id': 'parent_review_tool_free', 'phase': 'independent',
    'description': 'This parent source-review request exposes no tools; this is not a statement about earlier task actions.'}]
_, errors, _ = evaluate_response(raw, ParentReviewSnapshot(candidate), comparison=False)
candidate.update(repair=list(errors), repair_response=raw, repair_phase='independent', repair_used=True)
body = json.loads(ParentReviewSnapshot(candidate).bundle().messages[0].content)
assert body['repair']['previous_response'] == raw
assert 'child_advisory' not in body and 'child_objective' not in body
patch = {'replacements': [{'path': '/findings/3/runtime_refs', 'value': ['parent_review_tool_free']}]}
parsed, after_errors, effective = evaluate_response(json.dumps(patch), ParentReviewSnapshot(candidate), comparison=False)
assert not after_errors
original = json.loads(raw.strip().removeprefix('```json').removesuffix('```').strip())
assert [f['judgment'] for f in parsed['findings']] == [f['judgment'] for f in original['findings']]
assert 'any list-returning stub passes' in parsed['findings'][2]['judgment']
result = {'status': 'PASS_STRUCTURAL_REPAIR_ONLY', 'provider_calls': 0, 'fixture_execution': False,
    'source_attempt': owned.name, 'before_errors': before_errors, 'v2_errors': errors,
    'repair_has_exact_previous_response': True, 'repair': patch, 'after_errors': after_errors,
    'all_judgments_preserved': True, 'known_false_semantic_claim_remains': True,
    'semantic_acceptance': 'NOT_ACCEPTED', 'new_model_run': False}
(Path(__file__).parent / 'replay-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
