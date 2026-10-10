# Child-only v5 public dispatch preflight

Execution is PENDING_ROOT_REVIEW. The actual human authorization is the existing 1m/300k correction and continued iteration; the harness does not turn its one-attempt safeguard into a claim that the human permits only one API execution. Prior v4 (`next-version-s16-investigation-v4-dffd15671147`) remains a failed real attempt with five settled HTTP calls and no child. v5 preserves the same task kind, sources, model/effort, procedure, quality gates and budgets: parent cumulative 1m, child cumulative 300k, single prompt 300k, schema 20k, output 4096, shared 12 rounds / 40 tools / 8 per round, child 4 tools / 90 seconds, outer attempt 300 seconds. This sub-agent does not call Provider; Root reviews and records the human authorization before supervising execution.

Script: `run_real_investigation_v5.py`, SHA256 `4d4863e738e110fb65d1bf6f6559e186c8a91d3539efdbef3a78b1efb35a72ef`. All JSON reads explicitly use UTF-8, including fixture execution checks.

MAIN provisional owned: `owned-cases/next-version-s16-investigation-v5-769d65c55aac`. Actual prepare exit 0, stderr 0 bytes, `OFFLINE_PREFLIGHT_ONLY`, HTTP attempts 0, Provider calls 0. Its source is the current MAIN implementation, explicitly not a reviewed candidate; both live parent and worker gates forbid executing this owned case. Earlier MAIN preflights `...v5-618e81a69ec7` and `...v5-06df1d85bb5d` also remain preserved, with no HTTP.

Actual public `tasks.start` freezes the unchanged objective. The probe activates that frozen task, reads production EngineLimits, then calls the exact engine action dispatcher's public `dispatch` method for `load_tool_contract` and `delegate_agent`, with the task's frozen authorization and typed execution context. It no longer invokes `app.subagents.dispatch` directly. The ordinary schema, restricted mode/role, TaskScoped/Root policy, trusted agent catalogue and original ChildRunner all run. Original guarded client preparation is observed, then stopped before Provider send. No substitute Provider or child TaskRecord is created.

Observed original first prepared request: model `glm-5.3-flash`, reasoning effort `medium`, reservation 33,337, Host prompt ceiling 300,000, effective input cap 284,000. Actual frozen parent TaskBudget is 1,000,000; actual durable child binding and derived child TaskBudget are 300,000 tokens / 4 tools with matching parent task and owner. The probe explicitly tests original Sessions admission in its private preflight state, leaving a diagnostic pending reservation rather than calling or settling a Provider. That pending record is not actual consumption. Workspace source hashes are checked unchanged around dispatch. Prepared capacity does not prove source reading, successful real child terminal state or parent delivery.

After independent review and candidate update, clear `S16_PREFLIGHT_SOURCE` and run only prepare:

```powershell
Remove-Item Env:S16_PREFLIGHT_SOURCE -ErrorAction SilentlyContinue
& 'C:/Users/Windows11/.codex/worktrees/next-version-s2-baseline/chaos-16-agent/.venv/Scripts/python.exe' 'F:/code-ai-chaos/chaos-16-agent/docs/next-version/s16/run_real_investigation_v5.py' prepare
```

This creates a new candidate-bound owned fixture with a PENDING_ROOT_REVIEW authorization record. Root checks fresh HEAD, full-chain preflight, CI/build and independent review, then saves the existing human instruction and latest correction in a concrete owned authorization record. Code-task unchanged-source waiting-decision and advisory child completion remain separate real-result gates; the harness does not rewrite model answers or change TaskKind to manufacture completion.
