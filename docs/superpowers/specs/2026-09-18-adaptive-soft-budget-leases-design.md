# Adaptive soft budget leases design

## Goal

Reduce unnecessary model turns and tool calls on simple tasks without weakening
the agent's ability to complete deep, cross-module work. Keep the existing
50-round and 128-tool-call limits as final safety ceilings, and add auditable
soft leases that the agent can renew automatically when trusted runtime evidence
shows useful progress.

## Current problem

`EngineLimits` currently exposes one large task-wide ceiling. The Core has
separate guards for exact repeated reads, consecutive tool-only turns, repeated
validation failures, repair cycles, active time, and token use, but it has no
early checkpoint between starting a task and approaching the final ceiling.

The high ceiling is not itself an instruction to consume the full budget.
However, without an earlier progress checkpoint, an ordinary task can continue
exploring until a repetition or global-budget guard activates. Conversely,
replacing the ceiling with a small fixed limit would stop legitimate discovery
before the agent can establish that a reported one-file bug has a wider cause.

Permissions are a separate concern. The existing action policy already permits
recognized work inside the authorized workspace and preserves approval or deny
boundaries for protected paths, outside-workspace access, network access,
unknown tools, and critical actions. Soft leases must not grant or remove any
capability.

## Options considered

1. **Adaptive soft leases inside the existing hard ceiling (selected).** Start
   with a bounded working lease, renew it from trusted progress signals, and
   preserve the existing hard limits and action policy.
2. **Lower the hard limits by task class.** This is simpler, but a mistaken
   initial classification can irreversibly stop a complex task before useful
   evidence exists.
3. **Use a second model to review every renewal.** This resembles Auto-review
   systems, but adds latency, cost, and another failure mode before deterministic
   runtime signals have been evaluated in production.

## Selected design

### Hard ceiling and soft lease

The existing `EngineLimits` remain the only hard model-turn, tool-call, token,
and output limits. Exhausting them continues to pause the task before another
provider or external-action call.

Each persistent foreground task also has a soft lease with these cumulative
default thresholds:

| Lease | Model turns | Tool calls | Initial use |
| --- | ---: | ---: | --- |
| `quick` | 4 | 8 | Read-only questions and bounded explanations |
| `standard` | 12 | 30 | Normal modification and debugging tasks |
| `deep` | 30 | 80 | Explicit deep work or evidence-backed escalation |

The thresholds are internal defaults in the first release, not new user-facing
configuration. They will be calibrated with evaluation data before becoming a
public tuning surface.

At the `deep` threshold, a progressing task may receive one final extension to
the existing hard ceiling. No lease can exceed the task's frozen
`EngineLimits`. A profile with a lower hard limit clamps every lease threshold
to that limit.

### Initial lease selection

Initial selection is deliberately conservative and does not claim to know the
task's final complexity:

- Read-only questions and bounded explanation requests start at `quick`.
- Modification, investigation, and debugging requests start at `standard`.
- A user request that explicitly asks for deep, exhaustive, repository-wide, or
  cross-module work starts at `deep`.

Initial selection reuses the task's frozen intent and request facts. It does not
change the task's interaction mode, agent mode, model, tools, or permissions.

### Trusted progress signals

When the next model turn or tool reservation would cross the current soft
threshold, Core evaluates progress since that lease was issued. Renewal is
allowed when at least one of these Host-observed signals exists and no existing
stagnation guard has requested convergence:

- the workspace subject generation increased;
- a new verification run or a different validation-failure fingerprint was
  recorded;
- a successful write or structured verification action completed;
- a read-only action produced a call/result fingerprint not previously observed
  in the current lease;
- the task contract was revised by a promoted user follow-up or steering input.

Model prose, a model's claim that a task is complex, repeated identical reads,
repeated identical validation failures, and a tool call rejected before
execution are not progress evidence.

Read-only fingerprints are bounded digests of the canonical tool name,
arguments, and result. Raw tool output is never copied into the lease state.
Only a bounded recent digest set is retained so persistence cannot grow with the
number or size of tool results.

### Renewal and convergence

The lease decision is deterministic:

1. If the task is already at a hard limit, preserve the existing hard-limit
   pause behavior.
2. If an existing exact-repeat, tool-only, repeated-validation, repair-cycle, or
   active-time guard requires convergence or pause, do not renew the lease.
3. If trusted progress exists, advance `quick` to `standard`, `standard` to
   `deep`, or grant the single final deep extension.
4. If no trusted progress exists, queue one bounded runtime notice requiring the
   next response to resolve from current evidence. Analysis tasks summarize;
   modification tasks continue through the existing completion and verification
   gates and cannot claim completion without required evidence.

A lease checkpoint is not a permission prompt and does not wait for the user.
Rejected or unavailable actions remain available for an alternative approach
within the current lease; they do not justify renewal by themselves.

### Persistence and audit

The current lease is part of the persistent task budget, alongside cumulative
usage. Sessions stores:

- current lease level;
- cumulative turn and tool thresholds;
- renewal count and whether the final deep extension was used;
- bounded progress fingerprints and the progress baseline for the lease;
- the last renewal or convergence reason.

Budget reservation, checkpoint fork, resume, and rewind preserve these fields.
Old databases migrate to a lease derived from the frozen task intent and current
usage; migration never reduces the existing hard limit or resets usage.

Lease decisions reuse `TASK_BUDGET_WARNING` with a `category` of `lease` and a
phase of `renewed` or `converge`. The payload contains only the previous and new
lease, cumulative usage, and a stable reason code. It contains no prompt, tool
output, path contents, or model reasoning.

The existing cost/status projection displays the current lease and renewal count
from persisted facts. There is no new permission mode, modal approval, or
task-mode picker.

## Component boundaries

- **Core** owns initial lease derivation, bounded progress observation, renewal
  decisions, runtime notices, and interaction with existing convergence guards.
- **Sessions** atomically persists lease state with task budget reservations and
  carries it through migration, resume, checkpoint fork, and rewind.
- **Interfaces** only project persisted lease facts in existing status and cost
  views; they do not decide renewal or infer progress.
- **Policy, Runtime, and Verification** keep their existing authority. Lease
  changes cannot weaken action decisions, create verification evidence, or
  describe local execution as an operating-system sandbox.
- **Providers and capability disclosure** remain unchanged. A larger lease does
  not reveal more tools or alter provider protocol limits.

## Failure behavior

- A persistence conflict fails closed before the additional model turn or tool
  call. Resume reloads the last committed lease and cumulative usage.
- Unknown or corrupt lease values fail task restoration rather than silently
  resetting the budget. Legacy rows without lease fields use the migration rule.
- A renewal event and its updated budget state are committed atomically; neither
  may appear without the other.
- Cancelling during a lease checkpoint follows the existing durable interrupt
  path and cannot consume an unreserved model turn or tool call.
- The final hard ceiling always wins, even if progress evidence exists.

## Verification

- Unit-test initial lease selection for read-only, modification, and explicit
  deep requests.
- Test asymmetric boundaries immediately below and at every turn/tool threshold.
- Verify novel read evidence renews a lease while an identical call/result
  fingerprint does not.
- Verify a new subject generation, a new verification run, and a distinct
  validation failure renew independently; rejected calls and model prose do not.
- Verify `quick → standard → deep → final extension`, clamping under lower
  profile limits, and refusal beyond the hard ceiling.
- Verify existing exact-repeat, five-turn tool-only, repeated-validation,
  repair-cycle, token, and active-time guards take precedence over renewal.
- Verify lease state survives repository reopen, resume, checkpoint fork, and
  rewind without resetting cumulative usage.
- Verify legacy database migration derives a valid lease for tasks whose usage
  is already above `quick` or `standard` thresholds.
- Verify audit events contain stable bounded facts and no tool output or model
  reasoning.
- Add evaluation scenarios for a simple answer, a bounded one-file change, and a
  cross-module failure. Compare completion rate, user interventions, model
  turns, tool calls, premature convergence, and hard-limit pauses against the
  current behavior.

## Non-goals

- Adding a second-model renewal or safety reviewer.
- Changing action permissions, approval prompts, sandboxing, or network policy.
- Removing or raising the existing hard limits.
- Making lease thresholds a public configuration surface in the first release.
- Treating token use, model confidence, response length, or elapsed tool count
  alone as evidence of progress.
