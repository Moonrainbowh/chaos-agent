# S16 MCP Python 3.10 compatibility

MCP Feature now uses a same-task deadline on Python 3.10 while preserving the native `asyncio.timeout` implementation on Python 3.11+. The final unchanged-budget verification passed all 33 MCP tests on both 3.10.20 and 3.13.2. These are local Windows Feature results; Linux CI and the frozen candidate remain Root responsibilities.

## Change

- `src/code_agent/mcp/deadlines.py`: 3.10 delegates cancellation and deadlines to the established `async-timeout` backport, with its separate `asyncio.TimeoutError` normalized to builtin `TimeoutError` for the existing public and health contracts. 3.11+ directly imports native `asyncio.timeout`.
- `lifecycle_owner.py` and `stdio_manager.py`: replace all 10 native-only deadline sites with the helper. Startup, calls, ping, shutdown and watchdog budgets remain identical. The idle queue separately catches `asyncio.TimeoutError`, which is distinct from builtin `TimeoutError` on 3.10.
- `tests/test_deadlines.py`: six counterexamples covering same-task cleanup; external cancellation remaining `CancelledError`; inner and outer nested deadlines; removal of a normal-exit timer; and a persistent SDK-style AnyIO task group whose startup, ping, call and cross-task requested shutdown all run in the original owner. The idle queue must reach ping and remain healthy.
- `src/code_agent/mcp/AGENTS.md`: documents the supported backend and exception boundary.

The wrapper adds no timer implementation, protocol logic, second SDK task, or AnyIO timeout scope. A surrounding AnyIO `fail_after` cannot safely exit while SDK contexts entered during startup retain their own nested CancelScopes. The backport's official implementation uses the current Task: [async-timeout 5.0.1 source](https://github.com/aio-libs/async-timeout/blob/v5.0.1/async_timeout/__init__.py).

Root separately authorized and owns the conditional `async-timeout==5.0.1; python_version < '3.11'` dependency and lock update. This Feature task did not modify root dependencies, CI, remote tests, candidate, commits or remotes.

## Actual verification

All commands set `PYTHONPATH=F:/code-ai-chaos/chaos-16-agent/src;F:/code-ai-chaos/chaos-16-agent` and run `python -m unittest discover -s src/code_agent/mcp/tests -p 'test_*.py' -v` from the main workspace. Fixture children use their own `TemporaryDirectory`; no Host, real project database, provider or paid call is used.

| Runtime | Interpreter | Actual result | Evidence |
| --- | --- | --- | --- |
| 3.13.2 | `C:/Users/Windows11/.codex/worktrees/next-version-s2-baseline/chaos-16-agent/.venv/Scripts/python.exe` | 33 tests, 20.078s, exit 0 | `py313-mcp-fix.log` |
| 3.10.20, fresh venv first run | `C:/Users/Windows11/.codex/artifacts/next-version-s16-python310/Scripts/python.exe` | 33 tests, 26.837s, 2 startup errors, exit 1 | `py310-mcp-first.log` |
| 3.10.20, unchanged rerun | same interpreter | 33 tests, 26.257s, exit 0 | `py310-mcp-fix.log` |

The first 3.10 run failed in startup for the busy-queue and unknown-write tests, before their intended call behavior: the first owner's synchronous cold SDK import took 1.625s against the existing 1.5s startup budget; another fixture initialization also reached that budget. Four SDK memory-stream ResourceWarnings were observed in that failed run. The unchanged rerun's corresponding import took 0.516s and all tests passed without ResourceWarnings or unobserved-task diagnostics. No deadline, assertion or skip was changed between the two runs, and the failed evidence is preserved. The passing rerun does not establish fresh-environment timing stability.

The 27 existing tests include real official-SDK handshake/discovery failures, startup cancellation, transport pre-yield cancellation, short shutdown budgets, cross-task close, repeated caller cancellation, unknown writes without replay, generation/source revocation and busy queue preservation. All six new deadline tests passed in both 3.10 runs and in 3.13.

`py310-mcp-runtime.log` confirms 3.10.20, async-timeout 5.0.1 and absolute main-workspace owner/helper module paths. The 3.13 probe confirmed those same main source paths and native `asyncio.timeouts` backend. Local tests load main source with candidate 3.13 dependencies, not candidate source.

No full-suite, Linux CI or final release acceptance is claimed here. Root will independently review and synchronize the candidate before those checks.

## Cold SDK import diagnosis requested by Root

Switching the fixture to SDK low-level `Server` would not remove the Web-stack imports. The installed locked SDK `mcp/__init__.py` imports `server.session`; package initialization first executes `mcp/server/__init__.py`, which imports `FastMCP` immediately. An independent process probe confirmed all three import routes load FastMCP, Starlette and 599 modules:

| Import route | Import seconds | Whole process seconds |
| --- | --- | --- |
| Official client | 0.618 | 0.731 |
| FastMCP | 0.592 | 0.705 |
| Low-level Server | 0.609 | 0.724 |

Evidence: `py310-mcp-import-probe.json`. These measurements use separate 3.10 processes after filesystem/bytecode caches were warmed by verification; they do not reproduce the first fresh-venv cold import. No fixture rewrite was made because the proposed low-level route gives no import reduction.

Root applied the minimal test preparation fix: import the official SDK client in synchronous fault-test setup before starting the lifecycle deadline. This avoids serially adding parent SDK cold imports to the real child's startup/handshake deadline while preserving the product's lazy import, the 1.5s budget, actual official-SDK subprocesses and all failure/cancellation assertions. The child still imports and initializes under the unchanged deadline.

## Verification after Root's SDK test preparation

The complete MCP Feature suite passed in new interpreter processes on both runtimes: 3.10.20 **33 tests / 26.499s / exit 0** (`py310-mcp-prepared-sdk.log`), 3.13.2 **33 tests / 19.417s / exit 0** (`py313-mcp-prepared-sdk.log`). No ResourceWarnings or unobserved-task diagnostics appeared. The first failed fresh-venv run remains recorded above; no test budgets or assertions were relaxed.

`probe_mcp_cold_start.py` separately starts a fresh process with no `mcp` SDK imported before `manager.start`, uses the product's unmodified defaults (start 15s / call 60s / close 6s), and runs an actual official-SDK child with an explicit temporary root. It verifies handshake and tool discovery, one successful tool call with exactly one marker, shutdown, absence of the fixture-owned PID and temporary-root removal.

| Fresh process | Cold SDK start seconds | Call seconds | Close seconds | Result |
| --- | --- | --- | --- | --- |
| 3.10.20 | 1.257 | 0.0034 | 0.045 | PASS, child stopped |
| 3.13.2 | 0.799 | 0.0029 | 0.047 | PASS, child stopped |

Exact timings, original-owner source path and PID cleanup evidence are in `py310-mcp-cold-start.json` / `.log` and `py313-mcp-cold-start.json` / `.log`. These are SDK-module-cold fresh-process checks with existing filesystem/bytecode caches, not new-installation or Linux/macOS CI claims. The probe adds no product changes or timeout overrides.
