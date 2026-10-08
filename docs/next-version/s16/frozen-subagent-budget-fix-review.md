# Host frozen parent budget handoff: local verification

Scope: MAIN Host bug only. Candidate `463dc98099e3275a70daf896507b02d4ae387ae9` and prior paid attempts remain unchanged. No Provider call was made. User's corrected budget is parent cumulative 1,000,000 / child cumulative 300,000; these are distinct from the 300,000 single-prompt guard and persistent window capacity. Cumulative exhaustion does not reset through compression or a new window.

Production `compose_subagents` now supplies a resolver reading the active typed parent's persisted `TaskBudget`. Its frozen token/tool ceilings establish the first task-owned child ledger. Explicit `ParentBudget` only tightens these ceilings; other limits remain unchanged. Missing typed lineage or frozen budget, mismatched task, foreign owner/origin fail closed before creating the ledger. A lock spans the first resolver await. Task-owned ledgers survive supervisor release/resume and retain charged known usage, including the known lower bound returned with incomplete usage. This ledger does not retain the full requested lease after that result settles. Persistent unknown reservations remain the responsibility of the existing Sessions guard; release/resume does not clear those records. Runtime close clears the cache. Unconfigured/taskless behavior retains the original 200,000 default.

Verification used actual production composition, real temporary SQLite sessions, frozen running parent tasks and public child budget binding. A local engine event source supplies observations; this is an offline root regression, not real-model acceptance. No profile/config/credentials/real database was changed.

Python 3.13.2 and 3.10.20 both ran `python -m unittest discover -s tests -p 'test_subagent*.py'`: 20 tests, exit 0. Both imports were separately confirmed to resolve MAIN `chaos_agent/subagents.py` and MAIN `src/code_agent/sessions/repository.py`. The 11 new cases cover production 1m-to-300k admission, smaller frozen ceiling rejection, frozen tools, explicit narrowing, missing context pollution prevention, first/cached owner and task mismatch, concurrent first dispatch, resumed settled use, and resumed incomplete usage's known lower bound. In the latter case, returned known usage is 250k despite no durable usage record: 800k cannot fit the remaining 750k. It does not prove that the full 300k lease remains held. Independent review separately exercised a real Sessions pending 800k reservation: child 250k admission was rejected before and after resume, and the pending record remained unchanged; that is the evidence for the durable unknown guard. Logs: `frozen-subagent-budget-python313.log`, `frozen-subagent-budget-python310.log`. Original red evidence: `frozen-subagent-budget-red.log`.

The initially attempted `.venv-regression` run was Python 3.10 with stale installed package resolution and failed imports; it was not a valid 3.13 run. The final two logs above use the verified runtimes and explicit MAIN `PYTHONPATH`.

SHA256 snapshot for independent review:

| Path | SHA256 |
| --- | --- |
| chaos_agent/subagents.py | 88722a2924e2886e2bcc9673c11fee8e924455e8a9b271984401678388f9a8dd |
| chaos_agent/host_composition.py | 51acb12e11d9c9906feebd4910c59b3092dc8d8a5f4ed8bb5e2bcc800774f655 |
| chaos_agent/AGENTS.md | a8daf90f14bcc58b6714ba67d3f87004a67f3aaf361511cda4d671a814c39915 |
| tests/test_subagent_frozen_budget.py | 30649e7c1b505f479e95f0cf18ab49383f0e6bd7398b39f7788229516aed8a62 |

These are local scoped results. Fresh independent review, candidate CI/build and the public v4 HTTP0 preflight remain Root's subsequent gates. Nothing here turns the prior real-model failures into a pass.
