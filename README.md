# Chaos Agent

`Chaos Agent` is a Windows-first, open-source coding agent with a shared headless core, an append-only Windows Terminal UI, and non-interactive CLI/JSON modes.

It is a clean-room implementation. It takes architectural lessons from projects such as uv-agent, Aider, Cline, OpenCode, mini-swe-agent, OpenHands, and Goose, but does not copy their source code.

## What It Does

- Uses OpenAI Responses, OpenAI Chat Completions, or Anthropic Messages through one streaming model protocol.
- Keeps sessions, messages, events, goals, and checkpoints in a versioned SQLite database.
- Discovers hierarchical `AGENTS.md` rules, builds a bounded repository map, and compacts history deterministically.
- Freezes `low`, `medium`, `high`, or `ultra` task modes to an actual provider profile, model, prompt policy, tool set, reasoning effort, and execution limits. Modes never grant permission.
- Runs bounded advisory Subagent, Oracle, Review, Search, and Librarian children through the same typed tools, policy checks, cancellation tree, and cumulative parent budget.
- Loads trusted declarative plugins without executing plugin Python, shell, URLs, or terminal control sequences. Typed tool contributions are live; event, command, mode, custom Agent, and Host-interaction declarations are validated snapshots pending their remaining Host wiring.
- Routes file reads, edits, Git inspection, structured local verification, and PowerShell commands through typed tools, central policy checks, audit events, and explicit approval.
- Provides a Windows Terminal TUI (`chaos-agent`), a text CLI (`chaos-agent ask`), session resume (`chaos-agent resume`), and machine-readable events (`chaos-agent run --json`). The legacy `agent` command remains available during migration.

## Install

```powershell
python -m pip install .
```

For development, install the runtime dependencies and run the test suites:

```powershell
python -m pip install -e .
python -m unittest discover -s src\code_agent\core\tests -p 'test_*.py'
```

## Configure A Provider

Create `%LOCALAPPDATA%\chaos-agent\config.toml` to configure a provider once for the current Windows user. An existing `%LOCALAPPDATA%\code-agent\config.toml` is read when the new file is absent:

```toml
[default]
provider = "openai"

[providers.openai]
api = "responses"
base_url = "https://api.openai.com"
model = "gpt-4.1-mini"
api_key = "replace-with-your-key"
context_window = 128000
max_output_tokens = 16384

[agent]
approval_mode = "ask"
# Optional for a single-user trusted local setup:
# approval_mode = "full-local"
# allow_sensitive_paths = true
```

Every configured `[providers.<name>]` profile must declare `api`, `base_url`, `model`, exactly one of `api_key`/`api_key_env`, `context_window`, and `max_output_tokens`. Use `chaos-agent --profile <name>` or `CHAOS_PROFILE` to choose one; `CHAOS_CONFIG` may select another absolute config path. `CHAOS_API`, `CHAOS_BASE_URL`, `CHAOS_MODEL`, and `CHAOS_API_KEY_ENV` override only the selected profile. Legacy `CODE_AGENT_*` names remain fallback aliases during migration.

`--mode low|medium|high|ultra` or `CHAOS_MODE` selects the task mode. By
default every mode uses the selected provider profile, while
`CHAOS_MODE_LOW_PROFILE`, `CHAOS_MODE_MEDIUM_PROFILE`,
`CHAOS_MODE_HIGH_PROFILE`, and `CHAOS_MODE_ULTRA_PROFILE` can bind individual
modes to other configured profiles. The mode/profile/model snapshot is frozen
for the task; changing access policy is a separate operation.

The API key is never written to a session or project file. The local file is suitable only for a personal Windows user: another process running as the same user can theoretically read it after explicit approval. Windows Credential Manager is a possible future enhancement, not a current dependency. The file tool rejects the local configuration directory even when it is used as a workspace.

The environment-only flow remains supported:

```powershell
$env:OPENAI_API_KEY = "..."
$env:CHAOS_API = "responses"
$env:CHAOS_BASE_URL = "https://api.openai.com"
$env:CHAOS_MODEL = "gpt-4.1-mini"
```

Profile limits belong in the TOML provider table. In the terminal, `/模式`
shows the current mode and each mode's bound model; `/模式
low|medium|high|ultra` rebuilds the main runtime while idle and applies to the
next task. It never accepts a URL, protocol, API key, or permission change.

Provider selection values:

| `CHAOS_API` | Protocol |
| --- | --- |
| `responses` | OpenAI Responses API |
| `chat_completions` | OpenAI-compatible Chat Completions |
| `anthropic_messages` | Anthropic Messages API |

Use `CHAOS_API_KEY_ENV` when the key variable is not `OPENAI_API_KEY`.

## Run

```powershell
chaos-agent
chaos-agent ask "inspect this repository" --model fast
chaos-agent --profile fast ask "inspect this repository"
chaos-agent --mode high ask "review this repository and run the relevant tests"
chaos-agent ask "inspect this repository and explain the test layout"
chaos-agent resume <thread-id>
chaos-agent resume <thread-id> "continue the previous task"
chaos-agent run --json "list the relevant files"
```

The Windows UI appends completed user, agent, tool, diff, warning, and error
entries to the normal Windows Terminal buffer. Windows Terminal owns selection,
copying, and scrollback. The default visual profile uses Unicode symbols,
medium transcript spacing, cyan emphasis, dim-gray tool records, and green
only for task-level completion. The live tail keeps a single bordered composer;
typing `/` places keyboard-selectable command candidates above it; `Up`/`Down`
move, `Enter` completes or selects, and `Esc` closes the Picker. Disabled
candidates retain a visible reason. `/帮助` prints the compact, grouped command
set, while `/帮助 <command>` explains one command. The status row keeps dynamic
work on the left and model/elapsed context on the right when space allows.
`NO_COLOR` disables ANSI color.

Skills are discovered only from `%USERPROFILE%\.agents\skills\<id>` and
`<workspace>\.agents\skills\<id>`, each containing `SKILL.md` with optional
frontmatter. Matching IDs and digests merge their sources; different digests
are isolated as conflicts. Workspace Skills require explicit activation and
cannot register tools, execute scripts, call the network, or change policy.

MCP configuration uses approved, structured stdio entries under
`[mcp.servers.<name>]`: `command`, `args`, optional `cwd`, an environment-name
allowlist, `tool_risks`, plus `enabled` and `approved`. Approved servers are
started through the installed SDK, expose only tools with a local
`read`/`write`/`network`/`critical` risk mapping, and route through the same
policy and approval boundary as built-in tools.

Declarative plugins are discovered from
`%LOCALAPPDATA%\chaos-agent\plugins\<id>\plugin.json` and
`<workspace>\.chaos-agent\plugins\<id>\plugin.json`. Activation requires the
manifest SHA-256 digest to match `%LOCALAPPDATA%\chaos-agent\plugin-trust.json`.
Plugin tools map only to known Host typed actions; both the plugin declaration
risk and the mapped Host action risk must pass policy. Invalid namespaces,
conflicts, unknown mappings, digest changes, and untrusted manifests are
isolated without disabling built-in tools.

## Safety Defaults

- `CHAOS_APPROVAL_MODE=ask` is the default. Workspace reads run automatically; a single external file read requires TUI approval and external writes are denied.
- `plan` allows workspace reads only. `auto` remains compatible and requires approval for outside-workspace access. `elevated` requires approval for each external read, write, or recursive enumeration. `full-local` permits typed external file operations, while commands still require approval.
- `allow_sensitive_paths = true` (or `CHAOS_ALLOW_SENSITIVE_PATHS=true`) is a separate explicit opt-in for `.env` files and private-key names. `.git`, `.code-agent`, and symlink/reparse paths remain protected at every level.
- Unknown and critical actions are denied. Destructive commands and unbounded output are rejected.
- `delegate_agent` is a normal typed action. It is policy checked before a child starts; child output is explicitly advisory and never counts as verification evidence or parent completion.
- `run_command` always represents model-provided raw PowerShell and requires explicit approval, including inside a foreground task. `run_verification` only accepts a registered kind plus constrained relative paths; the local adapter generates its fixed argv for Python unittest, pytest, compileall, or build.
- The local runtime is controlled process execution, not an OS-level sandbox. Typed verification executes user-authorized project code under the current Windows user and therefore does not isolate that code's indirect filesystem or network effects. Docker is optional and uses no network and no image pulls.

Sessions are stored at `%LOCALAPPDATA%\chaos-agent\sessions.sqlite3` by default. When that target is absent and the legacy `%LOCALAPPDATA%\code-agent\sessions.sqlite3` exists, Chaos Agent uses SQLite backup into a temporary target, checks integrity and key counts, then atomically publishes the copy while retaining the legacy database.

## Foreground Tasks

Engineering requests in the Windows TUI run as durable foreground tasks. A task
is scoped to its current workspace and may perform ordinary workspace edits and
non-network local tests without a prompt for every action. Network access,
paths outside the workspace, unknown tools, and critical commands remain
blocked or require an explicit decision.

Use `Esc` to pause and `/任务` to inspect tasks. Ordinary input submitted while
a task is running queues guidance for that same task. Recovery uses `/会话` and
`/恢复 <thread-id>`. Process commands are
`chaos-agent task list` and `chaos-agent task resume <task-id> [instruction]`.
Paths, commands, model names, Git refs, and raw tool data are never translated.

Bracketed multi-line paste normalizes CRLF/CR to LF and inserts one bounded
input block without submitting it. Pressing `Ctrl+C` once pauses/rejects/clears
according to current state and arms a two-second exit window; only a second
`Ctrl+C` exits the TUI.

Running-task guidance is shown as `queued`, `steered`, `dequeued`, and
`applied`. The last two states advance only after persisted `TURN_STARTED` and
`CONTEXT_BUILT` events prove that Core consumed the control and rebuilt model
context. Approval prompts remain visible above the composer, show action,
target, risk, and reason, default to `No`, and require `Enter` or `Esc`.
`/diff` prefers a fresh diff from the injected read-only Git adapter and renders
file statistics plus bounded unified diff lines.

Tasks persist lifecycle state, checkpoints, and cumulative budgets. Closing the
terminal, sleep, hibernate, shutdown, or reboot does not keep work running;
the next foreground session resumes from a checkpoint and never replays an
in-flight command. This release deliberately has no daemon, remote observer,
background continuation, worktrees, automatic commit, or push.

## Context Budgets And Local Diagnostics

Each model context has a deterministic 20,000-token configured ceiling. The
default allocations are 3,000 shared by the system prompt and rendered project rules, 1,500 for tool
schemas, 1,000 for structured task state, up to 2,000 for the repository map,
up to 12,000 for messages, and a 500-token safety reserve. The repository map
shrinks before message history, which retains at least 2,000 tokens. A project
rule set that exceeds its 3,000-token allocation fails locally with a typed
rule-limit error before any provider request is made; rules are never silently
truncated.

Repository-map scans use a process-local, 5,000-entry LRU cache keyed by file
size and nanosecond modification time. It is cleared on process restart and a
successful workspace write invalidates that path, so unchanged files can be
reused while the next turn reparses changed files.

Resumable task state records durable, reducer-derived facts such as files read
or changed, command outcomes, and verified observations. Model-authored
working notes and open questions are bounded separately and are explicitly
rendered as unverified; they do not become facts merely by being stored.

Every `CONTEXT_BUILT` session event contains local numeric counters only:
configured prompt budget, rule/tool/task-state/repository-map/message
allocations, removed-message count, and repository-cache hits and misses. It
contains no prompt text, project rules, source text, command output, API-key
names, or external telemetry. Agent-round and tool-call limits are intentionally
retained from the existing model profile and persistent budget work; this
feature measures their context environment for later evidence-based tuning and
does not tune those limits.

The semantic checkpoint, bounded thread index, authorized thread-tree search,
and revision-aware `read_thread` Feature are implemented and tested, but the
current `ContextBuilder.build(...)` integration protocol does not carry a
`thread_id`. Chaos Agent therefore keeps deterministic compaction active in the
runtime rather than generating checkpoint anchors with a fabricated thread
identity. The required interface revision is recorded in
`docs/amp-inspired-runtime.md`.

## Development Status

The first release targets Windows 10/11. The core protocol and adapters are portable, while Linux/macOS end-to-end terminal/runtime support remains future work.

## License And Acknowledgements

Released under the [MIT License](LICENSE).

This project acknowledges the public design work of uv-agent, Aider, Cline, OpenCode, mini-swe-agent, OpenHands, and Goose. They are references for problem framing and user experience, not code sources for this repository.
