"""Parse literal source values, without executing the fixture or its tests."""
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = HERE.parent / 'fast-acceptance/attempts/s16-source-completion-p4-74e53e65f9c7'
path = RUN / 'workspace/test_names.py'
raw = path.read_bytes()
tree = ast.parse(raw.decode('utf-8'))
blank = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'test_blank')
assertion = blank.body[0].value
inputs = ast.literal_eval(assertion.args[0].args[0])
expected = ast.literal_eval(assertion.args[1])
summary = {'fixture_sha256': hashlib.sha256(raw).hexdigest(),
    'source_line_6': raw.decode('utf-8').splitlines()[5],
    'input_values': inputs, 'input_codepoints': [[ord(c) for c in s] for s in inputs],
    'expected': expected, 'last_value_is_tab': inputs[-1] == '\t',
    'last_value_is_literal_backslash_t': inputs[-1] == '\\t',
    'fixture_executed': False, 'provider_calls': 0}
(HERE / 'fixture-literal-audit.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
