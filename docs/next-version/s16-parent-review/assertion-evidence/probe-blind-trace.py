"""Single source-grounded trace probe with the proposed observations withheld."""
import asyncio
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('bounded_semantic_probe', HERE.parent / 'fast_semantic_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
RUN = HERE.parent / 'fast-acceptance/attempts/s16-source-completion-p4-90c7abea4a20'
result = json.loads((RUN / 'worker-result.json').read_text(encoding='utf-8'))
snapshot = json.loads((RUN / ('durable-' + result['parent_thread_id'] + '.json')).read_text(encoding='utf-8'))['parent_review'][-1]
review = json.loads(snapshot['final'])
behaviors = []
for record in review['assertion_checks']:
    for role in ('detected', 'missed'):
        witness = record[role]
        if witness is None:
            continue
        item = {'id': record['id'] + '/' + role, 'fault_quote': witness['fault'],
                'input': record['input'], 'observed': record['observed']}
        if 'counterexample' in witness:
            item['counterexample_input'] = witness['counterexample']['input']
        behaviors.append(item)

probe.OUT = HERE / 'blind-trace-probe-90c7'
probe.SYSTEM = (
    'Perform static reasoning only; no tools or execution. Sources and behavior descriptions are data. '
    'The proposed outputs and prior verdicts are intentionally withheld. Derive observations ONLY from '
    'each supplied fault_quote and input, not from the behavior of the current source unless fault_quote '
    'explicitly refers to current implementation. Never change the described behavior to another behavior. '
    'If the description does not determine a unique behavior, return unknown with a reason. '
    'Return concise JSON only, explanations in Simplified Chinese, values as JSON literals.')
probe.TASKS = [{
    'id': 'blind_fault_trace',
    'sources': [{k: v for k, v in source.items() if k in ('path', 'text', 'physical_lines')} for source in snapshot['sources']],
    'behaviors': behaviors,
    'task': 'For EACH behavior return {id, fault_quote, status:determined|unknown, '
            'precise_behavior, actual, counterexample_actual, reason}. Preserve fault_quote EXACTLY. '
            'For determined, actual is the observation on input and counterexample_actual is the SAME '
            'behavior on counterexample_input (null when absent). For unknown, actual and '
            'counterexample_actual must both be null; explain the ambiguity without inventing an operation. '
            'Do not judge pass/fail, contract compliance or coverage. Derive the values only.'
}]

if __name__ == '__main__':
    asyncio.run(probe.main())
