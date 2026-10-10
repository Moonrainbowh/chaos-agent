# S7 writable child audit — CHANGES_REQUESTED

Locked CPython 3.13, actual production `app.subagents._runner` factory/Core/write_file and real SQLite Sessions reproduced a missing parent mutation projection. No product code or candidate was changed during reproduction.

1. Parent changes README.md and receives genuine Host planner documentation attestation; assessment is VERIFIED, generation 1.
2. Writable child creates new_module.py with `VALUE = 2`; its own TaskState records the file.
3. Parent TaskState still lists only README.md. Assessment remains VERIFIED with identical generation, subject hash and old verification run.
4. Transitioning parent to VERIFYING then invoking real ledger finalize succeeds: parent becomes COMPLETED without verification of child code.

Evidence: `child_write_repro.py`, `child-write-repro.json`. The result is a real mutation/evidence gap, not advisory text promoted into evidence: the child text is ignored, but the new source file never invalidates existing evidence.

Code path: `core/engine_actions.py:215-218` reduces child state, and task=None skips verification recording. `sessions/_task_records.py:136` only updates own thread. `verification/task_service.py:211` snapshots parent state; `workspace/subject.py:35-38` selects changed paths and manifests only, including in Git workspaces. Thus a new child source path is omitted; already tracked parent paths can change the hash on assess, but still do not advance generation via child execution.

Required fix: project actual frozen-owner child mutation facts atomically into parent state, advance parent generation, invalidate subject, retain old append-only evidence as historical only, and ensure duplicate action replay does not advance twice. Child advisory text and run_verification claims must remain excluded. Current execution-instance mismatch must reject settlement; in-flight cleanup while same owner is paused/stopping must be supported. Stale parent snapshots must not overwrite concurrent child invalidation.

This report records pre-fix evidence. Root requested implementation after the gap was reproduced; subsequent validation is recorded separately.

## Implemented correction and validation

Main workspace now projects frozen-owner child mutation facts and an idempotent `context:child_action` receipt into parent state in the same SQLite transaction. Actual writes/partial edits/attempted commands expire generation and subject; advisory text and child run_verification do not become evidence. Same paused owner can settle; replaced execution instance and changed-result replay reject. Parent stale snapshots cannot drop concurrent actual child paths or precede the latest child mutation generation; readonly bindings leave generic state save semantics unchanged.

The same real production reproduction now yields parent generation 1→2, changed files README.md + new_module.py, changed subject, UNVERIFIED assessment, and blocked finalize. Old evidence remains historical. See `child-write-repro-after.json`.

Locked CPython 3.13: 15 focused tests PASS (`child-write-focused-313.log`), complete Sessions 231 tests / 2 skipped / no failures or errors (`child-write-sessions-313.log`). CPython 3.11: eight new targeted tests PASS (`child-write-focused-311.log`). No candidate modifications, commit, or push.

## Additional race closure

The generic guard's equal-generation/same-path boundary was reported to root and then closed. Production Verification captures the full durable TaskState before reading file bytes and publishes via `save_task_state_if_current` SQLite CAS. A deterministic test hashes the old file, settles a real child mutation to that same path on another thread (exactly the parent's proposed generation), then attempts the old snapshot save: CAS rejects and preserves the child mutation. No blind retry occurs.

The race test also exposed that completion's existing transaction compared caller generation/hash to the selected verification run but omitted the latest durable TaskState. The transaction now rejects missing current state and stale generation/subject. Existing completion fixtures persist the subject; they additionally test absent and stale state rejection. Passing an old VERIFIED assessment after child mutation can no longer finalize the parent.

After both corrections: locked CPython 3.13 Sessions 232 tests / 2 skipped / no failures or errors (`child-write-cas-sessions-313.log`), Verification 67 PASS (`child-write-cas-verification-313.log`), ten new focused tests PASS (`child-write-cas-focused-313.log`). CPython 3.11 ten new focused tests PASS (`child-write-cas-focused-311.log`). Existing command behavior conservatively expires generation without guessing arbitrary produced paths, as parent commands already do.
