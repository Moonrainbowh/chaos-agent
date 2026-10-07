"""Save P2 offline checks without overwriting P0/P1 evidence."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

root = Path(__file__).resolve().parents[3]
output = Path(__file__).parent
checks = (
    ('disclosures', ['-m', 'unittest', 'code_agent.capabilities.tests.test_catalog',
        'code_agent.capabilities.tests.test_compact_tools',
        'code_agent.core.tests.test_engine_tool_disclosures',
        'tests.test_builtin_tool_prompt_budget', '-q'], 0),
    ('stage-regression', ['-m', 'unittest', 'tests.test_s16_source_completion', '-v'], 1),
)
reports = []
for label, arguments, expected_exit in checks:
    command = [sys.executable, '-X', 'utf8', *arguments]
    environment = dict(os.environ, S16_P0_ARTIFACT_DIR=str(output / 'p2-wire-evidence'))
    result = subprocess.run(command, cwd=root, env=environment, capture_output=True,
                            text=True, encoding='utf-8')
    log = result.stdout + result.stderr
    (output / ('p2-' + label + '.log')).write_text(log, encoding='utf-8')
    report = {'label': label, 'command': command, 'exit_code': result.returncode,
              'expected_exit_code': expected_exit,
              'test_count': int(re.search(r'Ran (\d+) tests?', log)[1]),
              'outcome': log.strip().splitlines()[-1]}
    if expected_exit:
        report['only_preserved_source_gate_red'] = (
            report['outcome'] == 'FAILED (failures=1)' and
            'RED: production ChildResult reports completion after disclosure with zero source reads' in log)
    reports.append(report)
    print(json.dumps(report))
(output / 'p2-run.json').write_text(json.dumps({'cwd': str(root),
    'provider_network_requests': 0, 'checks': reports}, indent=2), encoding='utf-8')
raise SystemExit(0 if all(report['exit_code'] == report['expected_exit_code']
    and report.get('only_preserved_source_gate_red', True) for report in reports) else 1)
