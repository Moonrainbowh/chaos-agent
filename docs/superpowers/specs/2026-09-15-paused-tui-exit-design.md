# Paused TUI exit design

## Goal

Let a Windows Terminal user exit a TUI that has already reached `paused`
without holding `Ctrl+C` or relying on the two-key confirmation window.

## Observed problem

The existing `ExitGuard` applies the two-press confirmation before inspecting
the task state. In the reported Windows Terminal session, repeated `Ctrl+C`
input was repeatedly shown as a new first press and the TUI only exited after
the key was held. The screenshot confirms that the task was already `paused`.

## Options considered

1. **State-aware immediate exit (selected).** When the task status is
   `paused`, one `Ctrl+C` sets `running` to false. Active tasks preserve the
   current first-press pause and second-press exit behavior.
2. Keep the global two-press guard and change its timing or debounce policy.
   This still depends on Windows Terminal delivering two distinct events and
   does not improve the already-paused case.
3. Treat every `Ctrl+C` as an immediate exit. This makes recovery simpler but
   removes the existing safeguard against accidentally closing a live task.

## Design

`handle_interrupt` will check the terminal task status before calling
`ExitGuard.interrupt`. For `paused`, it will set `app.running = False`, cancel
an in-memory token if present, and return without appending the confirmation
prompt. Other states keep the existing modal handling, pause behavior, input
clearing, and two-press exit semantics.

No checkpoint, task-status, or persistence behavior changes: normal TUI
shutdown continues through `close_tasks` and its durable interrupt path.

## Verification

- Add a unit test that a paused app exits after one `Ctrl+C`.
- Keep the existing tests that require two `Ctrl+C` events to exit from other
  states and while a pause is settling.
- Run the affected interface test modules.
