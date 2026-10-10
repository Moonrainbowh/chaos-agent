"""Persist genuine test output and prove imports target this isolated checkout."""
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[3]
output = Path(__file__).parent
sys.path[:0] = [str(root / 'src'), str(root)]
import chaos_agent.runtime_dispatcher_factory as factory
import code_agent.core.engine as engine
import tests.test_s16_source_completion as regression
imports = {module.__name__: str(Path(module.__file__).resolve())
           for module in (factory, engine, regression)}
assert all(Path(path).is_relative_to(root) for path in imports.values()), imports
command = [sys.executable, '-X', 'utf8', '-m', 'unittest', 'tests.test_s16_source_completion', '-v']
environment = dict(os.environ, S16_P0_ARTIFACT_DIR=str(output / 'p0-wire-evidence'))
result = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True, encoding='utf-8')
(output / 'p0-red.log').write_text(result.stdout + result.stderr, encoding='utf-8')
report = {'cwd': str(root), 'python': sys.executable, 'imports': imports, 'command': command,
          'exit_code': result.returncode, 'provider_network_requests': 0,
          'scope': 'Production tasks.start, delegation dispatcher/factory/context/engine/ChildResult; HTTP MockTransport only'}
(output / 'p0-run.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
print(result.stderr[-1800:])
raise SystemExit(result.returncode)
