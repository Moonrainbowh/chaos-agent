# Resume Thread Picker Design

## Goal

Let a terminal user browse prior persisted conversations and resume one without manually copying a thread ID.

## Scope

- Add a `resume` interaction that lists persisted threads ordered by most recent activity.
- Render each option with its title or fallback identifier, last-message preview, updated time, and message count.
- Reuse the existing picker keyboard behavior for filtering, navigation, cancellation, and Enter-to-select.
- Resume the selected thread by validating it, reloading its durable transcript, replacing only the current transient UI transcript, and binding the selected `current_thread_id`.
- Support `/resume <thread-id>` as the direct thread-opening path; do not expose a `restore` command.

## Non-goals

- Do not auto-resume the most recent thread at startup.
- Do not alter persisted messages, task state, runtime contracts, or thread ordering.
- Do not add thread deletion, archiving, or a new storage format.

## Design

`/resume` delegates to the existing session-history controller and transforms durable thread summaries into shared picker items. The picker is populated only after a successful list operation. It uses the same filtering and key handling as other TUI selectors.

On selection, the resume path is invoked with the selected durable thread ID. It must load the complete persisted history before replacing the active UI state. If loading or runtime restoration fails, the current transcript and thread binding remain unchanged and the error is rendered in-band. A successful resume clears only temporary presentation state, replays durable messages, and binds the selected thread for subsequent turns.

## Verification

- Unit-test list rendering and metadata for multiple persisted threads.
- Unit-test filtering, selection, cancellation, and direct resume behavior.
- Verify restoration replays messages and changes `current_thread_id` only after successful loading.
- Verify a missing or invalid thread leaves the current thread and transcript untouched.
- Run focused interface tests and the relevant TUI command suite.
