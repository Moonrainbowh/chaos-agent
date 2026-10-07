"""Persist P1 checks separately; preserve the P0 before evidence."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

root = Path(__file__).resolve().parents[3]
output = Path(__file__).parent
checks = (
    ('context', ['-m', 'unittest', 'discover', '-s', 'src/code_agent/context/tests', '-p', 'test_*.py', '-q'], 0),
    ('completion', ['-m', 'unittest', 'code_agent.core.tests.test_completion_idle', '-q'], 0),
    ('integration', ['-m', 'unittest', 'tests.test_prepared_context_assembly',
        'tests.test_production_child_factory', 'tests.test_child_authorization_ceiling',
        'tests.test_child_execution_scope', 'tests.test_child_result_contract',
        'tests.test_runtime_partial_build_cleanup', '-q'], 0),
    ('stage-regression', ['-m', 'unittest', 'tests.test_s16_source_completion', '-v'], 1),
)
reports = []
if sys.argv[1:]:
    reports = json.loads((output / 'p1-run.json').read_text(encoding='utf-8'))['checks']
for label, arguments, expected_exit in checks:
    if sys.argv[1:] and label not in sys.argv[1:]:
        continue
    command = [sys.executable, '-X', 'utf8', *arguments]
    environment = dict(os.environ, S16_P0_ARTIFACT_DIR=str(output / 'p1-wire-evidence'))
    result = subprocess.run(command, cwd=root, env=environment, capture_output=True,
                            text=True, encoding='utf-8')
    log = result.stdout + result.stderr
    (output / ('p1-' + label + '.log')).write_text(log, encoding='utf-8')
    report = {'label': label, 'command': command, 'exit_code': result.returncode,
              'expected_exit_code': expected_exit,
              'test_count': int(re.search(r'Ran (\d+) tests?', log)[1]),
              'outcome': log.strip().splitlines()[-1]}
    reports = [previous for previous in reports if previous['label'] != label] + [report]
    print(json.dumps(report))
(output / 'p1-run.json').write_text(json.dumps({'cwd': str(root),
    'provider_network_requests': 0, 'checks': reports}, indent=2), encoding='utf-8')
raise SystemExit(0 if all(report['exit_code'] == report['expected_exit_code'] for report in reports) else 1)
