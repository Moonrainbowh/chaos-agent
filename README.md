# Chaos Agent

`Chaos Agent` is a Windows-first, open-source coding agent with a shared headless core, an append-only Windows Terminal UI, and non-interactive CLI/JSON modes.

It is a clean-room implementation. It takes architectural lessons from projects such as uv-agent, Aider, Cline, OpenCode, mini-swe-agent, OpenHands, and Goose, but does not copy their source code.

## What It Does

- Uses OpenAI Responses, OpenAI Chat Completions, or Anthropic Messages through one streaming model protocol.
- Keeps sessions, messages, events, goals, and checkpoints in a versioned SQLite database.
- Discovers hierarchical `AGENTS.md` rules, builds a bounded repository map, and creates source-anchored semantic checkpoints near context pressure with deterministic fallback.
- Freezes `low`, `medium`, `high`, or `ultra` task modes to an actual provider profile, model, prompt policy, tool set, reasoning effort, and execution limits. Modes never grant permission.
- Runs bounded advisory Subagent, Oracle, Review, Search, and Librarian children through the same typed tools, policy checks, cancellation tree, and cumulative parent budget.
- Loads trusted declarative plugins without executing plugin Python, shell, URLs, or terminal control sequences. Tools, namespaced commands and modes, custom Agents, typed events, and Host-owned interactions are wired through bounded controllers and policy checks.
- Uses a configurable `legacy` / `hybrid` / `progressive` tool-capability strategy. The recommended `hybrid` default preloads common built-in reads while progressively disclosing long-tail, plugin, and MCP schemas.
- Routes file reads, edits, Git inspection, structured local verification, and PowerShell commands through typed tools, central policy checks, audit events, and explicit approval.
- Provides a Windows Terminal TUI (`chaos-agent`), a text CLI (`chaos-agent ask`), session resume (`chaos-agent resume`), machine-readable events (`chaos-agent run --json`), and an ACP v1 editor adapter (`chaos-agent-acp`). The legacy `agent` command remains available during migration.

## Install

```powershell
python -m pip install .
```

For development, install the runtime dependencies and run the test suites:

```powershell
python -m pip install -e .
python scripts\run_tests.py
```

The full runner discovers every `src\code_agent\<feature>\tests` directory and
then runs the root integration suite. Use the Feature-specific `unittest
discover` command only for a focused edit; release and CI validation use the
full runner. Windows CI and release validation therefore cannot omit newly
added Features silently; the portable non-Windows job remains a smaller smoke
subset.

### Windows long paths

Chaos Agent does not change machine-wide registry or Group Policy settings.
For paths beyond the legacy-safe 240 UTF-16-code-unit budget, enable **Win32
long paths** (`LongPathsEnabled=1`) and restart Windows before starting the agent.
The current mode is shown by `/状态` and supplied to the coding model. When the
policy is disabled, typed workspace operations fail before a filesystem side
effect instead of silently omitting a deep file. Git for Windows is invoked
with per-command `core.longPaths=true`; this does not modify global, repository,
or user Git configuration. Managed Git worktree targets keep a separate
215-unit/UTF-8-byte cap because Git's `$GIT_DIR` check is not relaxed by that
setting. Snapshot roots are admitted against their longest derived blob path,
so a save cannot return a handle that is already unreadable.

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
# Optional USD rates used by /cost. Configure both or neither.
input_cost_per_million = 0.40
output_cost_per_million = 1.60
# Declare image only when this exact model/profile accepts image input.
input_modalities = ["text", "image"]

[agent]
approval_mode = "auto"
# Recommended default; alternatives are "legacy" and "progressive".
capability_strategy = "hybrid"
# Optional; "auto" probes PowerShell 7 first, then Windows PowerShell 5.1.
# powershell_dialect = "powershell_7"
# Optional alternatives:
# approval_mode = "ask"
# approval_mode = "unrestricted" # Explicit high-trust mode.
# allow_sensitive_paths = true
```

Every configured `[providers.<name>]` profile must declare `api`, `base_url`, `model`, exactly one of `api_key`/`api_key_env`, `context_window`, and `max_output_tokens`. Optional `input_cost_per_million` and `output_cost_per_million` rates must be configured together; `/cost` always reports durable task tokens and adds an estimated USD breakdown only when those rates exist. Use `chaos-agent --profile <name>` or `CHAOS_PROFILE` to choose one; `CHAOS_CONFIG` may select another absolute config path. `CHAOS_API`, `CHAOS_BASE_URL`, `CHAOS_MODEL`, and `CHAOS_API_KEY_ENV` override only the selected profile. Legacy `CODE_AGENT_*` names remain fallback aliases during migration.

`[agent].powershell_dialect` accepts `powershell_7` or
`windows_powershell_5_1`; omit it (or set `auto`) for the migration default.
The Host performs a bounded no-Profile probe at startup, verifies the actual
Edition/version, and freezes one executable for the source workspace and all
managed worktrees. An explicit dialect never falls back to the other one.
`CHAOS_POWERSHELL_DIALECT` overrides TOML and the legacy
`CODE_AGENT_POWERSHELL_DIALECT` remains a fallback alias. `/状态` shows the
full local selection, while Provider prompts receive only its basename and
verified dialect/version.

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

Profile limits belong in the TOML provider table. The default command panel
contains these 19 English commands (the `/` prefix remains compatible with the
primary `:` prefix):

```text
/clear  /compact  /cost  /status  /doctor  /exit
/diff   /map      /review   /test  /rewind  /attach
/model  /mode     /effort  /permission
/mcp    /plugin   /tasks
```

`/model` and `/effort` open searchable menus showing the current selection.
Use Up/Down to move, Enter to apply, Tab to complete, and Esc to return one
level. `/model <profile>` and `/effort <level>` also accept direct values.
They rebuild the runtime for the next task; if a paused or waiting task still
owns a frozen configuration, finish it or use `/new` before changing settings.
Command errors preserve the input for correction, and commands with missing
arguments show their usage. `/sessions history` opens a session picker.

The UI defaults to English. During workspace preparation, the status and
animation stay active and Esc cancels preparation. A completed task clears
the busy/queue indicator; a task lacking verification evidence shows
`Waiting for decision` with the missing evidence instead of staying busy.

`/mode ask|code|plan` controls the next task contract: `ask` and
`plan` disable workspace writes and local execution, while `code` remains
governed by `/permission auto|plan|ask|unrestricted`. `/compact` persists a
traceable semantic checkpoint when enough closed history exists; `/doctor`
runs bounded PowerShell, Git, workspace, path-policy, and provider TCP checks.
`/map` is the user-facing Unified Semantic Graph surface. Its secondary menu
provides `overview`, `context`, `impact`, `tests`, `risk`, `review`, `refactor`,
`locate`, and `dead-code`; every report shows the semantic generation and keeps
heuristic bug/dead-code results explicitly marked as candidates.

```text
:map overview src/code_agent
:map context "verification evidence"
:map impact src/code_agent/core/engine.py
:map tests src/code_agent/core/engine.py
:map risk src/code_agent/interfaces
:map review src/code_agent/core/engine.py
:map refactor src/code_agent/core/engine.py
:map locate "stale verification generation"
:map dead-code src/code_agent
:map overview src/code_agent/core/engine.py
:map overview src/code_agent --limit=50 --offset=50
```

Reports use the active task's workspace and refresh its **existing** shared
index (including externally edited, added, or deleted files). `context` and
`locate` combine that generation's lexical source matches and semantic edges;
no second scan cache or model call is created. Use an exact file path in
`overview` to inspect symbols with line numbers and dependencies/consumers.
Quote paths containing spaces. Aliases: `tree` = `overview`, `bug` = `locate`,
`dead` = `dead-code`. Optional `--limit=1..50` (default 12) and
`--offset=0..100000` page each section, with the total explicitly displayed;
use `--` before query text beginning with option-like flags.

Analysis runs on the full requested scope before display pagination. Change
commands accept up to 256 resolved files; broader scopes must be narrowed,
never silently truncated. Refactor plans separate cycle-dependent groups
instead of claiming a safe linear order. Dependency/call analysis currently
covers Python; other indexed languages have declaration/inventory support.
The index's file/parse limits and unresolved dynamic behavior still apply.
Bug and dead-code outputs are investigation candidates, not proven root causes
or deletion authorization. `map tests` recommends priorities without executing
tests; `map review` prepares scope, while `/review` starts the existing agent
review workflow. None of these advisory reports count as verification evidence.

Model and effort selections rebuild the main provider/runner while idle. The
hidden compatibility command `/mode agent single|team` still controls whether
bounded child-Agent delegation is exposed. The selected profile changes the actual provider model, while reasoning
effort is serialized into supported provider requests. The full runtime
selection is frozen into durable task contracts. The older `/mode
low|medium|high|ultra` forms remain hidden compatibility entries. Runtime
selection never accepts a URL, protocol, API key, or permission change.
`anthropic_messages` currently has no confirmed structured effort mapping:
its default `medium` remains prompt-only, other effort choices fail closed,
and the capability view labels this limitation instead of claiming it applied.

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
chaos-agent ask "explain this screenshot" --attach "C:\Screenshots\error.png"
chaos-agent --profile fast ask "inspect this repository"
chaos-agent --mode high ask "review this repository and run the relevant tests"
chaos-agent ask "inspect this repository and explain the test layout"
chaos-agent resume <thread-id>
chaos-agent resume <thread-id> "continue the previous task"
chaos-agent run --json "list the relevant files"
```

### ACP editor adapter

Editors that support Agent Client Protocol v1 can launch Chaos Agent as a
stdio process from the workspace root:

```json
{
  "command": "chaos-agent-acp",
  "args": ["--profile", "fast"]
}
```

`chaos-agent acp --profile fast` is the equivalent command. The adapter uses
the normal provider configuration and persisted Chaos session IDs. It supports
ACP initialization, session creation/listing/loading with message replay,
prompt streaming, tool status updates, and cancellation. Text and resource
links are accepted in prompts. Image/audio blocks, embedded resources,
additional workspace roots, client-provided MCP servers, editor terminal
proxying, and unsaved-buffer synchronization are not enabled in this first
version. Stdout is reserved for ACP JSON-RPC while the adapter is running.
New and loaded ACP sessions advertise `auto` and `session-all` modes. `auto`
keeps the normal trusted-workspace behavior, including recognized local
PowerShell. `session-all` applies explicit high-trust access only for that ACP
session and is cleared when the session, connection, or process closes;
unknown, critical, and protected actions remain blocked. ACP does not issue
per-action `request_permission` calls.

Tool contract disclosure is scoped to one Agent run. With the recommended
`hybrid` strategy, the first model turn sees `load_tool_contract`, a compact
name/category/summary directory, and the full provider definitions for
`read_file`, `read_code_slices`, `list_files`, `search_text`, `git_status`, and
`git_diff` when those built-ins are enabled. MCP, plugin, execution, editing,
coordination, and other long-tail tools remain progressive. A successful
contract lookup returns only `name`, a stable schema `digest`, and
`availability`; the complete JSON Schema appears only as a provider tool
definition on the next model turn. Disclosures are tracked only as tool name
plus schema digest, so an MCP or plugin removal or schema-changing reload
automatically hides the old definition until its current contract is loaded.
`legacy` sends all active tool definitions
without the loader, while `progressive` initially sends only the loader and
directory. Configure `[agent].capability_strategy` or
`CHAOS_CAPABILITY_STRATEGY`; the legacy `CODE_AGENT_CAPABILITY_STRATEGY` alias
remains available. Tool execution continues through the same typed dispatcher.
This deliberately stops short of a full Capability Manifest or lease layer;
that boundary can be revisited when the tool inventory grows materially or a
remote multi-user runtime creates a concrete need for generation and scope leases.

The Windows UI appends completed user, agent, tool, diff, warning, and error
entries to the normal Windows Terminal buffer. Windows Terminal owns selection,
copying, and scrollback. The default visual profile uses Unicode symbols,
medium transcript spacing, cyan emphasis, dim-gray tool records, and green
only for task-level completion. The live tail keeps a single bordered composer;
pressing `Shift+:` opens a bordered command panel with search plus aligned
command/description columns. `Up`/`Down` move, `Tab` completes, `Enter` executes
or opens a child menu, and `Esc` closes the Picker. The old `/` prefix remains
compatible. The root Picker contains the 19 commands listed above; `:help`
remains directly available as an advanced command, and `:help all` shows the
complete compatibility registry. Compound commands open a
second-level action menu instead of flattening every action into the root.
The status row keeps dynamic
work on the left and model/elapsed context on the right when space allows.
`NO_COLOR` disables ANSI color.

While a foreground task is running, `Enter` defaults to durable `[排队]`: the
message stays out of the current turn and is promoted FIFO when that turn
reaches its safe completion boundary. `Tab` outside a command panel toggles the
composer to `[转向]`; the next submission is persisted immediately and applies
at the next model boundary without hard-killing an active tool. `Esc` shows
`pausing` until cancellation and the pause checkpoint settle. Queued messages
survive pause, interruption, and process restart.

The default `auto` policy trusts recognized reads, writes, local PowerShell, and
shell-free processes inside the selected workspace. Network operations,
outside-workspace targets, protected paths/credentials, and irreversible
system-level operations keep their approval or denial boundary. Approval cards
default to reject and show action, target, risk, policy reason, and an
allow-once choice.

### Terminal appearance

New TUI instances use **Aurora**, a cyan rounded composer. Two alternatives are
**Ember** (warm amber, square frame) and **Mono** (neutral, open rules).
The themes cover responses, Markdown, commands, input, and task status.
They work with the terminal's existing dark background and font settings.

```text
:theme aurora
:theme ember
:theme mono
:theme motion off
:theme motion on
```

`:theme` lists the choices. Changes apply to the current process and future output;
existing scrollback remains selectable. To choose the startup theme in PowerShell:

```powershell
$env:CHAOS_THEME = 'ember'
chaos-agent
```

`CHAOS_REDUCED_MOTION=1` or `NO_COLOR` disables decorative transitions. Startup,
theme changes and task state changes use a short border transition; exit feedback
runs only after durable interruption has finished. Enter sends, Ctrl+J inserts a
newline; while running, Enter queues, Tab chooses steering, and Esc pauses.
The status shows task token totals without implying model capacity or remaining quota.

Compare all three themes and interaction states offline in
[the appearance preview](docs/ui-preview/index.html), or run the production renderer:

```powershell
python -m code_agent.interfaces.theme_preview --theme aurora --animate
python -m code_agent.interfaces.theme_preview --html docs/ui-preview/index.html
```

Preview conversations are illustrative and never call a model or execute tools.
Legacy `modern`, `symbol`, `signal`, and `plain` themes remain available.

### Same-machine session messaging

Open TUI instances for the same Windows user can exchange bounded plain text
through the shared local session database:

```text
/会话 在线
/会话 重命名 <name>
/会话 发送 <name-or-ref> <text...>
/会话 接收 auto|accept|hold|refuse
/会话 待处理
/会话 接受 <message-id>
/会话 拒绝 <message-id>
```

`/list-agents`, `/peers`, and `/rename` are hidden compatibility aliases. A
message contains no chat history, files, credentials, slash-command authority,
permission, or user approval. Text must fit 4 KiB after safe JSON escaping; a
message is acknowledged as delivered only when its complete text fits the
reserved peer context—never after silent truncation. It enters model context
only as token-bounded untrusted `PEER` JSON. Idle coordination turns use an
isolated internal thread and can call only `list_agents` and `send_message`, so
they neither consume nor replace the user's current task budget. Paused,
waiting, and task-owned threads wait for the user's next resume instead of
being restarted in the background. Permission mismatches can place messages
in `held` for explicit acceptance. This implementation is local to one Windows
machine and OS user; it is not a remote or cross-machine relay.

Skills are discovered only from `%USERPROFILE%\.agents\skills\<id>` and
`<workspace>\.agents\skills\<id>`, each containing `SKILL.md` with optional
frontmatter. Matching IDs and digests merge their sources; different digests
are isolated as conflicts. Workspace Skills require explicit activation and
cannot register tools, execute scripts, call the network, or change policy.
Use `/技能 列表|信息|来源|启用|禁用|重载` to manage the current thread's
persisted activation snapshot.

MCP configuration uses approved, structured stdio entries under
`[mcp.servers.<name>]`: `command`, `args`, optional `cwd`, an environment-name
allowlist, `tool_risks`, plus `enabled` and `approved`. Approved servers are
started through the installed SDK, expose only tools with a local
`read`/`write`/`network`/`critical` risk mapping, and route through the same
policy and approval boundary as built-in tools.
Use `/mcp list|status|tools|enable|disable|restart|diagnose` for the configured
server inventory; lifecycle changes publish a new tool generation only after
the SDK handshake succeeds.

Declarative plugins are discovered from
`%LOCALAPPDATA%\chaos-agent\plugins\<id>\plugin.json` and
`<workspace>\.chaos-agent\plugins\<id>\plugin.json`. Activation requires the
manifest SHA-256 digest to match `%LOCALAPPDATA%\chaos-agent\plugin-trust.json`.
Plugin tools map only to known Host typed actions; both the plugin declaration
risk and the mapped Host action risk must pass policy. Invalid namespaces,
conflicts, unknown mappings, digest changes, and untrusted manifests are
isolated without disabling built-in tools.
Commands and modes are always namespaced. Plugin events can only emit bounded
typed action proposals or `notify`/`confirm`/`input`/`select` requests owned by
the Host UI; action proposals pass both the declared plugin risk and mapped
Host action policy.
Use `/插件 list|status|enable|disable|reload` for the live plugin inventory.
Trusted manifests marked disabled on disk remain visible and can be enabled
explicitly; untrusted manifests stay isolated.
Enable and disable are idle-boundary operations. Reload discovered during an
active foreground task is staged and applied when that task settles; a
successful refresh updates commands, modes, tools, restricted dispatchers, and
policy risks together.

## Safety Defaults

- `CHAOS_APPROVAL_MODE=auto` is the default. Once the current workspace is selected, ordinary workspace reads, writes, non-critical local commands, and structured verification run without per-action approval. Network access, protected paths, and paths outside that workspace still require approval; unknown and critical actions remain denied.
- Use `:权限` (or the compatible `/权限` and English `permission` alias) while idle to select `unrestricted`, `plan`, `ask`, `auto`, `elevated`, or `full-local` for subsequent tasks. The same values are accepted by `[agent].approval_mode` and `CHAOS_APPROVAL_MODE`.
- Use `/权限 允许命令 [--network] <program> [args...]` to persist one exact `run_process_v1` rule for the current workspace. `/权限 规则` lists these rules and `/权限 撤销 <id-prefix>` removes one. A rule binds the resolved executable, complete argument list, workspace identity, descendant cwd scope, and network declaration; it never grants raw PowerShell.
- `plan` allows workspace reads only. `ask` approves writes and commands interactively. `auto` and `elevated` trust recognized actions inside the configured workspace. `full-local` also allows recognized non-critical local actions but asks at network and outside-workspace boundaries. Production typed file tools still fail closed at the workspace boundary; approving typed external-file access is not implemented yet.
- `unrestricted` is an explicit high-trust mode: recognized non-critical raw PowerShell and network actions run without per-action approval, and raw PowerShell can reach paths available to the current Windows user. Typed file tools remain workspace-contained, typed actions that explicitly target protected paths still require approval, and critical or unknown actions remain denied.
- `allow_sensitive_paths = true` (or `CHAOS_ALLOW_SENSITIVE_PATHS=true`) is a separate explicit opt-in for typed workspace file tools to access `.env` files and private-key names. It is not an OS sandbox: approved raw PowerShell, and raw PowerShell in explicit `unrestricted` mode, runs as the current Windows user and can bypass typed file guards. `.git`, `.code-agent`, local API configuration directories, cross-task `chaos-agent-workspaces` access, and symlink/reparse paths remain protected from typed file tools at every level.
- Configurations that omit `approval_mode` now resolve to `auto`. Set `unrestricted` explicitly only when the legacy high-trust behavior is intended.
- Unknown and critical actions are denied. Destructive commands and unbounded output are rejected.
- `delegate_agent` is a normal typed action. It is policy checked before a child starts; child output is explicitly advisory and never counts as verification evidence or parent completion.
- `run_command` represents model-provided raw PowerShell in the frozen dialect. Its UTF-8 wrapper preserves top-level `using`/`param`/`return`, uses `ErrorActionPreference=Stop` by default, and treats explicit catch/`Continue`/`SilentlyContinue`/`Ignore` as script-controlled recovery. It never sends native stdout/stderr through a PowerShell object pipeline, so raw bytes, missing final newlines, and control characters remain intact; genuine native stderr with exit zero succeeds, while the last nonzero native exit code has priority. `run_process_v1` instead accepts only `program`, literal `args`, optional workspace-relative `cwd`, a bounded timeout, and independent stdout/stderr encodings; it has no shell parsing, environment override, stdin, redirection, pipeline, glob, or variable expansion, and rejects shell launchers plus `.cmd/.bat`. In the default trusted-workspace flow, recognized non-critical commands run without per-action approval; network, protected-path, and outside-workspace signals still raise an approval card. Every actual attempt invalidates older verification evidence. `run_verification` accepts a registered kind plus constrained relative paths and lets the local adapter generate fixed argv for trusted verification.
- Process output defaults to strict UTF-8; BOM or an explicit UTF-8/UTF-16/Windows ANSI/OEM selection is decoded per stream. Undecodable or mixed output is reported with exact Base64 and code-page metadata rather than replacement characters, and output-limit metadata identifies the stream that actually lost bytes. Typed file reads return encoding/BOM/newline/code-page metadata; edits preserve existing UTF-8/16/32 BOM and consistent newline style, while new files default to UTF-8 without BOM. Legacy ANSI/OEM files require an explicit encoding.
- The local runtime is controlled process execution, not an OS-level sandbox. Typed verification executes user-authorized project code under the current Windows user and therefore does not isolate that code's indirect filesystem or network effects. Docker is optional and uses no network and no image pulls.
- On Windows 10/11, each local command gets an anonymous Job Object configured with `KILL_ON_JOB_CLOSE`. The root process is created suspended, identity-bound, assigned to the Job, and only then resumed. Timeout, cancellation, and output-limit termination target the Job first; assignment or Job API failures are reported instead of silently running without containment. The runtime waits for Job accounting to reach zero, closes the Job, and requires both pipe readers to reach EOF before returning; ordinary inherited children left after a normal root exit are terminated during the same finalization. This process ownership boundary is not an OS sandbox and does not claim to contain service-mediated or explicit breakaway execution.

Sessions are stored at `%LOCALAPPDATA%\chaos-agent\sessions.sqlite3` by default. When that target is absent and the legacy `%LOCALAPPDATA%\code-agent\sessions.sqlite3` exists, Chaos Agent uses SQLite backup into a temporary target, checks integrity and key counts, then atomically publishes the copy while retaining the legacy database.

## Replay Evaluation

The fixed replay corpus contains 40 versioned scenarios: 12 single-feature bug
repairs, 10 cross-file contract repairs, 10 idempotent recovery tasks, and 8
safety or read-only completion decisions. Repair fixtures contain genuinely
failing public checks plus hidden verifier checks; source-equivalent fixes are
accepted, while edited tests, extra files, stale evidence, replayed effects,
and model-authored success claims are rejected.

Each scenario runs from a disposable workspace. Baseline hidden-verifier clones
are removed before the executor starts, final verification uses a frozen
snapshot, and reports share one canonical scenario record and corpus
fingerprint. Missing SDKs, verifier timeouts, cleanup failures, and incomplete
trusted traces are reported as infrastructure or grading failures rather than
success. Live runs must use `ProcessScenarioExecutor` with a Host-trusted event
adapter; connecting its envelope directly to raw model output is unsupported
and intentionally fails closed when trusted trace evidence is absent.

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
Use `/attach <path>`, `/attach clipboard`, `/attach list`,
`/attach remove <index-or-digest>`, and `/attach clear` to manage the pending
attachment draft. When the clipboard contains a bitmap or one or more image
files copied from Explorer, `Ctrl+V` stages the whole image batch without
submitting (up to eight attachments total); ordinary text remains a normal
bracketed paste. A screenshot bitmap is one image, while browser HTML containing
several images is not split into separate attachments. Dropping complete
image/text file paths into Windows Terminal also stages them without submitting.
The draft stores only content-addressed references in messages; unsupported
image profiles fail before network I/O and retain the full draft.

`/diff` opens a point-in-time keyboard viewer over staged, unstaged, and
untracked changes. `↑`/`↓` and Page keys move lines, `←`/`→` switch files,
`[`/`]` move hunks, `/` filters paths, `c` adds a line comment, `r` refreshes, and `s`
sends all comments through the normal task/steering path. `Esc` or `Ctrl+C`
requires confirmation before unsent comments are discarded.

### Checkpoint And Rewind

`/checkpoint list` shows usable workspace checkpoints;
`/checkpoint create [label]` captures one manually. Managed Git tasks also
capture real workspace snapshots automatically at task creation and settled
lifecycle boundaries.
Before capture or restore, the runtime cancels the foreground execution tree
and waits for subagents and in-flight event handling to release the workspace.

`/rewind [checkpoint-id]` opens the canonical keyboard flow: choose a
checkpoint when needed, select code, session, or the combined mode, inspect the
bounded preview, and explicitly confirm execution. Rewind first records a recoverable
`pre-rewind` checkpoint, restores tracked and eligible untracked files from the
content-addressed snapshot, verifies the resulting inventory digest, and then
applies the requested session rewind. Checkpoints created by the older
metadata-only path remain stored but are omitted from this executable picker.

Tasks persist lifecycle state, checkpoints, and cumulative budgets. Closing the
terminal, sleep, hibernate, shutdown, or reboot does not keep work running;
the next foreground session resumes from a checkpoint and never replays an
in-flight command. Foreground coding tasks run in managed Git worktrees.
Durable checkpoints can restore tracked and eligible untracked code,
session/task state, or both. Ignored files, secrets, build outputs, Git
metadata, links/reparse targets, and in-flight commands are never captured or
replayed. Rewind keeps the original task history and requires an explicit
preview confirmation. This release deliberately has no daemon, remote observer,
background continuation, OS sandbox, automatic commit, or push.

## Context Budgets And Local Diagnostics

Each model context has a deterministic ceiling of up to 20,000 tokens, further
capped by the selected model profile's context window. The
default allocations are 3,000 shared by the system prompt and rendered project rules, 1,500 for tool
schemas, 1,000 for structured task state, up to 2,000 for the repository map,
up to 12,000 for messages, and a 500-token safety reserve. The repository map
shrinks before message history, which retains at least 2,000 tokens. A project
rule set that exceeds its 3,000-token allocation fails locally with a typed
rule-limit error before any provider request is made; rules are never silently
truncated.

Semantic compaction also budgets provider input before the request, reserving
space for its output and protocol overhead. If that budget cannot be satisfied,
it fails locally without provider I/O and falls back to deterministic summaries.

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

The Context Builder receives the active `thread_id`, reads stable message
sequences from SQLite, and persists semantic checkpoints plus searchable source
anchors. `search_threads` and revision-aware `read_thread` derive their caller
from Host execution context and enforce the persisted two-level parent/child
thread tree. Semantic service or persistence failure falls back to the existing
deterministic compactor.

Every foreground task also owns a durable Workflow DAG. `/流程`, `/流程
<node-id>`, `/流程 失败`, and `/流程 证据 <node-id>` render read-only snapshots
derived from trusted task, child-run, verification, invalidation, and delivery
observations; Workflow edges never grant thread access.

## Development Status

The first release targets Windows 10/11. The core protocol and adapters are portable, while Linux/macOS end-to-end terminal/runtime support remains future work.

## License And Acknowledgements

Released under the [MIT License](LICENSE).

This project acknowledges the public design work of uv-agent, Aider, Cline, OpenCode, mini-swe-agent, OpenHands, and Goose. They are references for problem framing and user experience, not code sources for this repository.
