# S7 independent final supervision

Candidate: SHA256 `c7289075e97495f3f713a612175e375e63a620badd09d3d2d14c14e1b7da380e`, 58 files, S6 base tree `8d0a5d4d816374731e7c01b6c42e6d0ea8f24a23`.

Verdict: **PASS** for this exact frozen S7 candidate. All original independent findings are closed, and required full-suite verification is complete. S7 may proceed to S8 under the approved serial plan; this verdict does not mark any later stage complete or authorize a push/release.

## Independent evidence

- Recomputed all 58 file SHA256 values in both main workspace and candidate checkout and the implementation patch SHA256: all match the frozen snapshot (`supervisor-hashes-final.json`).
- Candidate locked CPython 3.13.2; isolated TEMP workspaces and real SQLite repositories. No live model/network provider calls, live Host replacement, source edits, commits, pushes or release actions.
- `supervisor-independent-final.log`: 25 test executions, PASS, 5.293 seconds. Fourteen unique scenarios; eleven inherited base tests are intentionally also discovered as a second fixture class. Includes a new two-connection SQLite shared-budget race and exact-cap settlement scenario.
- `supervisor-focused-final.log`: 58 focused tests, PASS, 17.001 seconds. Includes real production/retained factory lineage and authority, owner CAS, budget/terminal truth, canonical and compact WRITE/EXECUTE in AUTO/FULL_LOCAL/UNRESTRICTED, real permanent process rules, plugin translated targets, network/outside boundaries and authority replacement rejection; cancellation draining threaded edits and asynchronous closer; actual parent-child mutation and same-path/equal-generation filesystem snapshot races.
- `supervisor-completion-final.log`: 20 repository tests, PASS, 2.738 seconds, including absent/stale durable subject refusal and valid current-generation finalization.
- `supervisor-child-write-final.json`: independently reran the original actual production repro. Parent now tracks README.md plus new_module.py, generation 1→2, subject changed; prior genuine verification evidence remains historical but completion assessment is unverified and finalization is blocked.

## Closure of initial findings

1. Retained exported runner now returns failed before allocating/calling a Provider when frozen authority is missing: independent original repro reports provider_calls=0, file_written=false, parent_tokens=0, parent_tools=0. The retained factory remains present. Its proper newer-runner composition binds durable shared budget and counts actual denied-action model/tool work. Both old and new factories install an immutable child authorization ceiling. Ordinary parent policy semantics are unchanged. Child canonical/compact and translated plugin actions are classified before inner policy/permanent-command approval can enlarge authority.
2. Actual child mutations transactionally update authoritative parent changed paths and generation, with stable idempotent receipts. Partial failed edits preserve uncertainty; attempted commands expire evidence; readonly/advisory outputs do not create verification evidence. Current owner can settle during pause/cleanup; a replaced execution owner cannot project old-child mutations. Snapshot publication uses complete-state SQLite CAS, including same-generation/same-path concurrent mutation; final completion transaction separately checks the current durable generation and subject hash rather than merely matching an old run and old assessment.
3. Partial cumulative Provider usage snapshots now replace the last observation for each request. Independent original Anthropic-shaped sequence 14/1→14/7 followed by cancellation reports 21 tokens, not 36, and remains incomplete. Durable request logs supply actual totals; duplicate settlement, partial liability, reopen and overrun facts remain conservative and cumulative.

## Cancellation/process evidence

Independent actual Windows process tests created five PID/create-time identities per tree, including venv launchers and parent/grandchild programs. Both parent cancel and active timeout observed the complete tree dead before closer completion, owner retained while the closer ran, release only after wait_all, and no late file write. Detailed identity evidence is retained in `supervisor-focused-final.log`. This is local Windows evidence; no new cross-platform CI push was authorized for S7.

## Final full-suite gate

Independently parsed the **last outer** `CHAOS_TEST_SUMMARY` from `all-tests-supervised.log`, rather than nested test-runner fixture summaries. Result: 30 suites, 3142 discovered and run, 30 skips, zero failures/errors/unexpected successes, exit_code=0, failed_suites=0, unrun_suites=[]. The root suite ran 609 tests. Exact parsed evidence is retained in `supervisor-full-summary-final.json`.

Rechecked both checkout projections for all 58 hashes and implementation patch hash after the complete run: unchanged and matching the frozen candidate. Inspected latest `end-validation.json`: original authentication diff remains unchanged, main/candidate projections match, and the final full-test summary corresponds to the successful run. No remaining actionable S7 issue found in the reviewed boundaries.
