# S16 fast real production acceptance

Run from the parent-review worktree after the final production change. No commit
is required: prepare freezes HEAD, tracked and untracked production file hashes,
the complete HEAD diff and runner hash as an explicitly dirty candidate.

```powershell
$prepared = .venv/Scripts/python.exe docs/next-version/s16-parent-review/fast-acceptance/run.py prepare | ConvertFrom-Json
.venv/Scripts/python.exe docs/next-version/s16-parent-review/fast-acceptance/run.py check --owned $prepared.owned
.venv/Scripts/python.exe docs/next-version/s16-parent-review/fast-acceptance/run.py execute --owned $prepared.owned
```

Prepare/check do not resolve credentials or call Provider. Execute is exclusive
(execution-started.json created with mode x), same GLM5.3-flash medium, original
four-source fixture and bytes, 300000/5/240 child budget, parent standard12/30,
hard12/40, 4096 output, 1000000 total tokens, original timeout60/retries2,
880-second cancellation and 900-second WindowsJob supervision. Job assignment
precedes stdin release to the model worker. No repeated attempt or full suite.

The worker is the original actual Host create_application/TaskService production
chain; wrappers only replace clean-HEAD provenance with the explicit manifest and
capture durable journals before application close. Source/settings/harness freeze
checks stay active. Parent full reads and both isolated stages consume the real
shared budget. Original synthetic preflight assumes old four parent requests and
unstructured final text, so is NOT_RUN; it is not reported as PASS.

Inspect exact real-wire-request-*.json, worker-result.json, real-supervisor.json,
real-events.jsonl, child journals and durable-<thread>.json (full persisted events,
messages and parent_review checkpoints). Match the parent thread ID from result.
Compare event IDs and usage request IDs for uniqueness rather than counting both
live stdout and durable replay as separate work. Require initial valid independent
checkpoint before comparison, identical source versions across stages, absence of
advisory/history/model child objective in first wire, complete advisory in second,
rendered Chinese delivery without raw JSON messages, and unverified final result.

Independent review must still judge all five actual code behaviors, test witnesses,
current/legacy distinction, physical lines, reconciliation and unknowns against
the original fixture. Exit0 and structure completion do not establish semantic
acceptance. Prior P4 reported two substantive errors; preserve failed new outputs.

Capacity risks remain real: original12 shared turns include child turns, only one
repair total, and4096 output can be insufficient. Do not enlarge budgets, truncate
sources, rerun or change model to manufacture acceptance. Dirty manifest checked
before execution and recorded after; compare fixture before/after hashes too.
