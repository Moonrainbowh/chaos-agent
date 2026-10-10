# Offline startup capture repair

The first owned directory `s16-source-completion-p4-f65a2823f406` retains its
real stderr and supervisor record. The factory adapter tried a method on the
WorkspaceSessionRouter class, whose dynamic forwarding supplies it only on an
instance. This AttributeError occurred before any Provider request or wire file:
it is a harness startup failure, not a model quality attempt.

Capture now patches the class of the actual bound repository behind the router.
The close collector passes the actual repository as self. Owner child routers
also delegate to that class.

Actual verification command from the worktree:

```text
.venv/Scripts/python.exe docs/next-version/s16-parent-review/fast-acceptance/run.py factory-check --owned docs/next-version/s16-parent-review/fast-acceptance/attempts/s16-source-completion-p4-f65a2823f406
```

Final result: exit 0, 1.538 seconds,
`OFFLINE_FACTORY_CLOSE_CAPTURE_PASS; Provider calls 0`.
It created the real application in the isolated preflight environment, created
parent and child threads, read through both routers, closed the real application,
and physically verified both captured durable JSON files. HTTP send was patched
to raise if attempted. No startup/model task or Provider was launched.
Run new prepare to freeze the repaired runner; do not reuse the old freeze.
