"""Offline audit of frozen artifacts against worktree and staged Git bytes."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
DOCS = Path(__file__).resolve().parent
checks = {}


def verify(path, expected):
    relative = path.relative_to(ROOT).as_posix()
    index = subprocess.run(['git', 'cat-file', 'blob', ':' + relative], cwd=ROOT,
                           check=True, capture_output=True).stdout
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, relative
    assert hashlib.sha256(index).hexdigest() == expected, relative + ' staged bytes'
    checks[relative] = expected


for case in sorted((DOCS / 'owned-cases-p4').glob('s16-source-completion-p4-*')):
    frozen = json.loads((case / 'freeze.json').read_text(encoding='utf-8'))
    for name, digest in frozen['workspace_hashes'].items():
        verify(case / 'workspace' / name, digest)
    for name, digest in frozen['settings_hashes'].items():
        verify(case / name, digest)
    for report in ('preflight-failure.json', 'preflight.json', 'worker-result.json'):
        path = case / report
        if path.exists():
            result = json.loads(path.read_text(encoding='utf-8'))
            for wire in result['wire']:
                verify(case / wire['body_file'], wire['body_sha256'])
    if (case / 'worker-result.json').exists():
        for name, digest in frozen['harness_hashes'].items():
            verify(DOCS / name, digest)

result = {'status': 'PASS_STAGED_CAPTURE_BYTES', 'unique_files': len(checks),
          'production_candidate': 'f8bb5dc75bd4808598e75100ee879248758ab9ce',
          'host_started': False, 'provider_calls': 0, 'sha256': checks}
(DOCS / 'delivery-byte-audit.json').write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({key: value for key, value in result.items() if key != 'sha256'}))
