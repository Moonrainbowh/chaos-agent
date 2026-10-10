# Persistent request capacity diagnosis

The two failures in `test_persistent_request_capacity.py` reproduce on the
unchanged `b28868a` baseline. They are not introduced by the parent-review patch.

The current tree's direct two-test command returned `Ran 2 tests in 0.969s`,
`FAILED (errors=2)`. A clean `git archive b28868a` extraction was loaded from
`C:\Users\Windows11\AppData\Local\Temp\s16-capacity-b28868a-selective`, with its
root and `src` placed first in `sys.path`. Both imported test and production
module paths in the actual traceback point to that baseline extraction.
The baseline returned `Ran 2 tests in 1.633s`, `FAILED (errors=2)` with the same
`input cannot fit without dropping required state; history retained` error.

Instrumentation wrapped `_persistent_bundle` only to print counts; it did not
modify the bundle, policy, counter, admission limits or results. Both trees gave
the same values:

| Input state | Scaffold UTF-8 bytes | System UTF-8 bytes | Logical estimate | Effective cap |
|---|---:|---:|---:|---:|
| One required user message | 6258 | 7619 | 8040 | 7900 |
| Fresh fallback, retaining required user | 6258 | 7735 | 8156 | 7900 |
| Long historical assistant message | 6258 | 7619 | 73157 | 7900 |
| Fresh fallback after long history | 6258 | 7735 | 8156 | 7900 |

The fake model name selects the documented UTF-8 byte upper estimate. The fixed
Windows Host scaffold plus persistent recovery instructions and protocol reserve
already exceed the small test ceiling; dropping the old assistant message cannot
make required input fit. The production capacity gate correctly rejects it.

Before the fixture repair, `git diff b28868a` was empty for the tested context/context_windows implementation,
`chaos_agent/tool_support.py`, `chaos_agent/context_runtime.py` and this test file.
The test workspace is an empty temporary directory, so the modified repository
AGENTS documents do not enter its rule loader. No Provider HTTP was attempted.

The diagnostic helper is
`C:\Users\Windows11\AppData\Local\Temp\s16_capacity_probe.py`; its output reports
the imported production module path along with each count. Baseline and current
checks use the same workspace `.venv/Scripts/python.exe` (Python 3.13.7).

Exact commands, run from the parent-review worktree:

```text
.venv/Scripts/python.exe -m unittest tests.test_persistent_request_capacity
.venv/Scripts/python.exe C:/Users/Windows11/AppData/Local/Temp/s16_capacity_probe.py C:/Users/Windows11/.codex/worktrees/s16-parent-review/chaos-16-agent
.venv/Scripts/python.exe C:/Users/Windows11/AppData/Local/Temp/s16_capacity_probe.py C:/Users/Windows11/AppData/Local/Temp/s16-capacity-b28868a-selective C:/Users/Windows11/AppData/Local/Temp/s16-capacity-b28868a-20261008.zip
```

The last command extracts only tracked `src/`, `chaos_agent/` and `tests/` paths
from the clean baseline archive before importing them. It does not import the
current checkout's corresponding packages. The baseline archive was created by
`git archive --format=zip --output <temporary archive path> b28868a`.

## Minimal fixture repair

Only `tests/test_persistent_request_capacity.py` changed: the positive fixture
now supplies a fixed short Host prompt at its existing public composition seam.
The Host total ceiling remains 8000 and the effective prepared-input cap remains
7900. All real provider preparation, byte counting, persistent composition and
admission code are unchanged. The positive cases still assert the actual prepared
request can fit, zero HTTP, zero usage reservations, unchanged original history
and the correct optional empty-carry fallback.

Within each of the same two existing test IDs, an additional oversized required
USER message must fail both real prepared preflight and persistent assembly;
its full history is retained and neither window state nor usage changes. No test
IDs were added or removed. The edit occurred after the ongoing full run had
already run this module; its original two errors are retained in full-tests.log.

After the fixture repair, the first command above returned `Ran 2 tests in
1.546s`, `OK`. This fixes an existing fixture's coupling to unrelated default
prompt length. It does **not** make the real complete Windows Host prompt fit
under 7900, weaken capacity rejection, enlarge a limit or establish real-model
token costs. The demonstrated baseline attribution is limited to the same
Windows/Python 3.13.7 environment and fake-model UTF-8 byte estimate.
