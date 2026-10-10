"""Replay saved failed review text through the gate; no model or fixture execution."""
import copy
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from code_agent.core.parent_review import ParentReviewSnapshot, evaluate_response

RUN = HERE.parent / 'fast-acceptance/attempts/s16-source-completion-p4-75e2fe91ce07'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


result = read(RUN / 'worker-result.json')
durable = read(RUN / ('durable-' + result['parent_thread_id'] + '.json'))
data = copy.deepcopy(durable['parent_review'][1])
snapshot = ParentReviewSnapshot(data)
raw = durable['parent_review_attempts'][1]['raw_output']
draft = json.loads(raw)
parsed, errors, effective = evaluate_response(raw, snapshot, comparison=True)
assert not errors
assert json.loads(effective) == draft
assert [f['judgment'] for f in parsed['findings']] == [f['judgment'] for f in draft['findings']]
body = json.loads(snapshot.bundle().messages[0].content)
groups = body['advisory_paragraphs']
assert [p['id'] for p in groups] == [2, 4, 6, 7, 8, 10, 11, 13, 15]
assert '### 代码 vs 契约' in next(p['text'] for p in groups if p['id'] == 6)
assert '### 测试 vs 契约' in next(p['text'] for p in groups if p['id'] == 10)
assert '### 覆盖缺口（审查要点）' in next(p['text'] for p in groups if p['id'] == 13)
assert body['child_advisory'] == data['advisory']

missing_body = copy.deepcopy(draft)
missing_body['comparisons'][0]['paragraph_ids'].remove(4)
missing_errors = evaluate_response(json.dumps(missing_body), snapshot, comparison=True)[1]
assert any('uncovered paragraphs: [4]' in e['message'] for e in missing_errors)

# A new citation error still uses the one field-patch protocol and rejects an empty patch.
bad_citation = copy.deepcopy(draft)
bad_citation['findings'][0]['citations'][0]['end_line'] = 999
bad_raw = json.dumps(bad_citation)
field_errors = evaluate_response(bad_raw, snapshot, comparison=True)[1]
repair_data = dict(data, repair=field_errors, repair_phase='comparison', repair_response=bad_raw, repair_used=True)
repair_snapshot = ParentReviewSnapshot(repair_data)
prompt = repair_snapshot.bundle().system_prompt
assert '"replacements"' in prompt and 'Return one JSON object: {"findings"' not in prompt
assert evaluate_response('{"replacements":[]}', repair_snapshot, comparison=True)[1]
patch = json.dumps({'replacements': [{'path': '/findings/0/citations/0/end_line', 'value': 2}]})
assert not evaluate_response(patch, repair_snapshot, comparison=True)[1]

# The persisted old v2 repair is now redundant. Only an explicit no-op can retain its draft.
old_repair = ParentReviewSnapshot(copy.deepcopy(durable['parent_review'][2]))
assert 'Return ONLY {"replacements":[]}' in old_repair.bundle().system_prompt
assert not evaluate_response('{"replacements":[]}', old_repair, comparison=True)[1]
assert evaluate_response(durable['parent_review_attempts'][2]['raw_output'], old_repair, comparison=True)[1]

summary = {
    'status': 'PASS_STRUCTURAL_REPLAY_ONLY', 'original_attempt': RUN.name,
    'original_errors': durable['parent_review_attempts'][1]['errors'], 'current_errors': list(errors),
    'required_group_ids': [p['id'] for p in groups], 'all_title_text_retained': True,
    'all_original_judgments_unchanged': True, 'missing_body_still_rejected': list(missing_errors),
    'field_patch_only_prompt': True, 'current_error_rejects_empty_patch': True,
    'stale_heading_repair_accepts_explicit_empty_patch': True,
    'historical_whole_rewrite_still_rejected': True, 'semantic_acceptance': 'NOT_ACCEPTED',
    'provider_calls': 0, 'fixture_execution': False,
}
(HERE / 'replay-result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
