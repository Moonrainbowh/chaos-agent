# Local Context Performance Metrics Design

## 1. Goal

Automatically collect local, privacy-bounded performance evidence that can
later determine whether Chaos Agent should implement persistent SQLite indexes
or deeper L0–L4 repository views.

Recording must never delay or fail a TUI task. Metrics are diagnostic evidence,
not correctness evidence, and they must not change Agent behavior.

## 2. Scope

This feature records:

- TUI session starts;
- repository index initialization, exact refresh, and reconciliation;
- context-build and Turn Repo Map timings;
- repository file and scan counts;
- index generation and view-cache outcomes;
- rendered Repo Map token counts;
- active mode names.

This feature also adds `/context-stats`, which summarizes the current
workspace's recent local measurements over 7-day and 30-day windows.

This feature does not:

- record prompts, task text, query terms, source code, symbols, or paths;
- record model responses, API configuration, tool arguments, Git diffs, or
  command text;
- send metrics over the network;
- use SQLite, a service process, a filesystem watcher, or an external daemon;
- automatically decide or start later Repo Map phases.

## 3. Storage and privacy

Metrics live outside the workspace:

```text
%LOCALAPPDATA%/chaos-agent/metrics/
├── install.key
└── workspace-context.jsonl
```

`install.key` contains 32 random bytes generated on first use. Creation uses an
exclusive create operation followed by an atomic rename; concurrent processes
that lose the creation race read the winning key. A stable anonymous workspace
identifier is:

```text
HMAC-SHA256(install_key, normalized_absolute_workspace_path)[:16]
```

The normalized path is never written to the metrics file. The identifier is
used only to group measurements from the same workspace across TUI restarts.
The random session identifier changes on every application start.

Each JSONL record contains `schema_version`, UTC timestamp, event name,
session identifier, anonymous workspace identifier, and only the numeric or
bounded enum fields defined below.

## 4. Architecture

Create a focused `src/code_agent/metrics/` Feature with its own `AGENTS.md`.

### 4.1 `PerformanceEvent`

A frozen typed value representing one validated event. Unknown fields, raw
paths, free-form text, negative durations, and unsupported enum values are
rejected before enqueueing.

Supported events:

| Event | Fields |
|---|---|
| `session_start` | `project_kind`, `repo_map_enabled`, `mode` |
| `index_update` | `update_kind`, `duration_ms`, `index_files`, `scanned_files`, `generation` |
| `context_turn` | `duration_ms`, `repo_view_duration_ms`, `view_cache_hit`, `rendered_repo_map_tokens`, `mode` |

`project_kind`, `mode`, and `update_kind` use fixed enums. No event accepts an
arbitrary message or metadata mapping.

### 4.2 `LocalMetricsRecorder`

The recorder owns a `queue.Queue(maxsize=256)` and one daemon worker thread.
Its public `record(event)` method:

1. validates or accepts an already validated `PerformanceEvent`;
2. calls `put_nowait`;
3. returns immediately;
4. catches every queue or lifecycle failure;
5. increments an in-memory dropped-event counter when the event cannot be
   queued.

No caller observes a recording exception. The recorder batches up to 32 events
and flushes at least once per second while active.

`close()` requests shutdown and waits at most 250 milliseconds. If the worker
does not finish, application shutdown continues because the thread is a
daemon. Metrics may be lost; TUI shutdown may not be delayed further.

### 4.3 `JsonlMetricsStore`

Only the worker thread writes the JSONL file. The store:

- creates the metrics directory lazily;
- writes UTF-8, one compact JSON object per line;
- runs retention maintenance after a batch when the file exceeds 5 MB or the
  last maintenance is older than one hour;
- retains only valid records from the most recent 30 days;
- when size trimming is required, keeps the newest valid records that fit
  within 5 MB;
- rewrites through a same-directory temporary file and atomic replacement;
- skips malformed records rather than failing the entire store.

The maintained file is at most 5 MB. A batch may exist only in worker memory
while maintenance runs.

### 4.4 `MetricsSummaryService`

The summary service reads the newest 5 MB tail of the file, discards an initial
partial line, skips malformed or unknown-schema lines, filters by the current
anonymous workspace identifier, and computes separate 7-day and 30-day
summaries.

Percentiles use deterministic nearest-rank calculation. Each window reports:

- sample and TUI-start counts;
- first-index P50 and P95 duration;
- exact-refresh and reconciliation P50 and P95 duration;
- P50 and P95 scanned-file counts;
- context-build and Turn View P50 and P95 duration;
- view-cache hit rate;
- average and P95 rendered Repo Map tokens;
- dropped events observed in the current process.

No automatic recommendation is emitted. These measurements are evidence for a
later human/Agent decision about Phase 3 or Phase 4.

## 5. Data flow and integration

`create_application` creates exactly one recorder and one workspace identity.
The same recorder is injected into contexts used by the main Agent, child
Agents, and mode-switched runners.

```text
Application start
  -> enqueue session_start

RepoIndexService.snapshot_for_turn
  -> measure update work with perf_counter_ns
  -> enqueue index_update after a published snapshot

WorkspaceContextBuilder.build
  -> measure total build and Turn Repo Map work
  -> enqueue context_turn after a successful ContextBundle

TUI /context-stats
  -> run summary read/calculation through asyncio.to_thread
  -> render bounded aggregate text
```

Index instrumentation counts calls to the single-file scanner during each
update. An unchanged snapshot that performs no update does not emit an
`index_update` event. Failed index or context operations do not emit successful
timing events; metrics failures never replace the original operation result.

Existing numeric `ContextBundle.measurements` remain model-turn event data.
The metrics recorder receives only a selected copy plus timing values and does
not serialize the bundle, prompt, or messages.

## 6. `/context-stats`

The command is available only as a local TUI command and does not call a model.
It shows the current workspace's anonymous aggregate, not raw JSONL records or
workspace identifiers.

Example shape:

```text
Context performance · last 7 days
sessions 8 · samples 42
first index p50 820ms · p95 2.4s
exact refresh p50 7ms · p95 18ms
context p50 3ms · p95 11ms
view cache 76% · repo map p95 940 tokens

Last 30 days: ...
```

With insufficient data, the command names the missing sample type. With an
unreadable store, it returns a short local diagnostic without a traceback.

## 7. Failure and concurrency rules

- Recording is best-effort and fail-open.
- Queue insertion never waits for disk or the worker.
- The worker is the only metrics-file writer in one process.
- Multiple Chaos Agent processes may append independently without an
  inter-process lock. Each append is one encoded line; readers skip partial
  lines, and retention uses atomic replacement. Concurrent append/retention
  races may lose diagnostic events, which is accepted rather than blocking a
  TUI task. A later maintenance pass restores the 5 MB bound.
- A malformed line, unsupported schema, clock-skewed future timestamp, or
  unavailable install key is skipped or regenerated without affecting Agent
  execution.
- Metrics are excluded from workspace discovery through their existing
  `%LOCALAPPDATA%/chaos-agent` location and sensitive-path protections.

## 8. Retention semantics

- Time retention: 30 days, based on UTC event timestamps.
- Size retention: 5 MB after worker maintenance.
- Maintenance cadence: at worker start, then at most once per hour unless the
  file exceeds the size limit.
- Deletion policy: invalid and oldest records first.
- There is no upload, backup, or migration into the repository.

## 9. Tests

### Metrics Feature

- event models reject free-form or sensitive fields;
- workspace identity is stable for one install key and does not expose paths;
- queue saturation returns immediately and increments the dropped count;
- disk errors and invalid directories never escape `record`;
- worker batches, flushes, and honors the bounded close timeout;
- 30-day expiry and 5 MB trimming preserve newest valid records;
- malformed lines and unknown schemas are skipped;
- 7-day and 30-day nearest-rank summaries are deterministic.

### Context integration

- initialization, exact refresh, and reconciliation record correct scan counts;
- unchanged snapshots emit no index-update event;
- context timing records cache hit/miss and rendered token counts;
- metrics failures do not alter snapshots or `ContextBundle`;
- main, child, and mode-switched contexts share one recorder.

### TUI integration

- `/context-stats` is discoverable and model-free;
- summary work runs off the terminal event loop;
- empty, partial, corrupt, and populated stores render bounded output;
- output contains no paths, prompts, task text, model/API details, or raw IDs.

## 10. Acceptance criteria

- No metrics producer performs blocking queue insertion or file I/O.
- Queue-full and disk-failure tests prove Agent tasks still succeed.
- TUI shutdown waits no more than 250 milliseconds for metrics.
- Metrics contain no user text, source facts, real paths, API information, or
  model output.
- The retained file is no older than 30 days and no larger than 5 MB after
  maintenance.
- `/context-stats` reports usable 7-day and 30-day aggregates.
- The recorded evidence is sufficient to compare cold index cost, incremental
  refresh cost, cache effectiveness, and TUI restart frequency before deciding
  whether to implement later Repo Map phases.
