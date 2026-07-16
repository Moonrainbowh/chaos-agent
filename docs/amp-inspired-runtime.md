# Amp-inspired Runtime Integration Record

This record separates ideas learned from Amp-like agent products from the
current Chaos Agent runtime. It is an implementation and acceptance record,
not a claim of source compatibility or copied behavior.

## Implemented And Integrated

| Area | Current behavior | Runtime evidence |
| --- | --- | --- |
| Modes | `low`, `medium`, `high`, `ultra` freeze profile, actual model, prompt policy, tools, reasoning effort, and limits. Mode never changes permission. | `--mode`, `CHAOS_MODE`, per-mode profile environment bindings, TUI mode/permission lines. |
| Child agents | Subagent, Oracle, Review, Search, and Librarian run in child threads with cancellation, concurrency, single-writer serialization, and cumulative parent reservations. | `delegate_agent` typed schema, central policy decision, advisory result marker, token/tool/time usage. |
| Plugins | Versioned JSON manifests validate digest, host API, trust, namespace, contribution conflicts, risk floors, and explicit enablement. | Trusted plugin tools are integrated as qualified definitions and checked against plugin risk and mapped Host risk. Other contribution snapshots are validated but still await Host routing. |
| TUI Picker | One bottom-oriented keyboard Picker model supports command, session, mode, Skill, MCP, and plugin item sources. | Command candidates are integrated; selection, completion, disabled reasons, error recovery, and `Esc` cancellation are tested. |
| Task truth | Running, verifying, paused, waiting decision, approval, partial, completed, failed, interrupted, and cancelled have distinct presentations. | Status rendering tests and persisted task events. |
| Steering | `queued`, `steered`, `dequeued`, and `applied` remain distinct with queue count. | `TURN_STARTED` proves dequeue; `CONTEXT_BUILT` proves application to rebuilt context. |
| Approval | Action, mapped target, risk, reason, No/Yes selection, `Enter`, and `Esc` are visible in the dynamic tail. | Host-owned approval broker; default selection is No. |
| Diff | Recorded typed write diff is parsed; an injected Git reader can replace it with current workspace diff. | File statistics, bounded unified lines, path filter, file navigation, and comment model tests. |

## Implemented But Not Yet Runtime-integrated

Semantic checkpoints, source anchors, bounded thread index/search, and
revision-aware `read_thread` are complete Feature Units. They are deliberately
not connected to `WorkspaceContextBuilder` yet. Its public `build(messages,
user_input, tools, task_state)` contract has no thread identity, so an
integration adapter cannot create truthful `SourceAnchor.thread_id` values.

The current runtime therefore retains deterministic compaction. The next safe
revision is to add an explicit context request carrying `thread_id`, context
pressure, cancellation, timeout, and task budget lease. After that change, the
semantic compactor can run near 90% pressure and persist its checkpoint through
the session repository without guessing identity or hiding model usage.

## Deliberate Differences

- Plugin code cannot register arbitrary Python callbacks, shell commands,
  process launches, URLs, ANSI output, or Web DOM.
- Child agent output cannot satisfy verification or completion gates.
- One foreground task remains visible in one append-only terminal column.
- Plugins, modes, Skills, MCP, and Oracle choices do not imply authorization.
- The app does not run a daemon, background continuation service, automatic
  worktree, commit, or push.

## Acceptance Snapshot

Recorded on 2026-07-15:

- 17 Feature suites: 515 tests passed.
- Root integration: 39 tests passed.
- Automated total: 554 tests passed.
- `python -m build --no-isolation`: wheel and sdist built successfully.
- `chaos_agent-1.0.2-py3-none-any.whl`: 381,150 bytes, SHA-256
  `C395189328D063C62B549CE80A11197C95CC12DB0BE8547D96B5DE42AA8215C8`.
- `chaos_agent-1.0.2.tar.gz`: 281,548 bytes, SHA-256
  `03528D28C3098E4BC4F673FBFAF35A5CF9F64303121B15FD4C7264051B508AFA`.
- Wheel inspection confirmed the mode, subagent, plugin, Picker, diff,
  orchestration, and thread-intelligence modules. Direct wheel import succeeded.
- Invalid CLI mode smoke check returned the expected usage error before runtime
  construction.

Not included in this automated acceptance: a paid/live provider child-agent
run, an installed third-party plugin, or hands-on Windows Terminal checks of
every narrow-width Picker/approval/diff interaction. Those remain manual gates,
not implied by the passing unit and integration suites.
