# Chaos Agent

`Chaos Agent` is a Windows-first, open-source coding agent with a shared headless core, an append-only Windows Terminal UI, and non-interactive CLI/JSON modes.

It is a clean-room implementation. It takes architectural lessons from projects such as uv-agent, Aider, Cline, OpenCode, mini-swe-agent, OpenHands, and Goose, but does not copy their source code.

## What It Does

- Uses OpenAI Responses, OpenAI Chat Completions, or Anthropic Messages through one streaming model protocol.
- Keeps sessions, messages, events, goals, and checkpoints in a versioned SQLite database.
- Discovers hierarchical `AGENTS.md` rules, builds a bounded repository map, and compacts history deterministically.
- Routes file reads, edits, Git inspection, and PowerShell commands through typed tools, central policy checks, audit events, and explicit approval.
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

[agent]
approval_mode = "ask"
# Optional for a single-user trusted local setup:
# approval_mode = "full-local"
# allow_sensitive_paths = true
```

Multiple `[providers.<name>]` profiles are supported. Use `chaos-agent --profile <name>` or `CHAOS_PROFILE` to choose one; `CHAOS_CONFIG` may select another absolute config path. `CHAOS_API`, `CHAOS_BASE_URL`, `CHAOS_MODEL`, `CHAOS_API_KEY_ENV`, and `CHAOS_APPROVAL_MODE` override the selected profile. Legacy `CODE_AGENT_*` names remain fallback aliases during migration. A profile may use either `api_key` or `api_key_env`, not both.

The API key is never written to a session or project file. The local file is suitable only for a personal Windows user: another process running as the same user can theoretically read it after explicit approval. Windows Credential Manager is a possible future enhancement, not a current dependency. The file tool rejects the local configuration directory even when it is used as a workspace.

The environment-only flow remains supported:

```powershell
$env:OPENAI_API_KEY = "..."
$env:CHAOS_API = "responses"
$env:CHAOS_BASE_URL = "https://api.openai.com"
$env:CHAOS_MODEL = "gpt-4.1-mini"
```

Profile limits belong in the TOML provider table. The terminal `/模型 列表` and
`/模型 使用 <profile>` commands expose only configured profiles; switching is
allowed only while idle and never accepts a URL, protocol, or API key.

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
chaos-agent --model fast ask "inspect this repository"
chaos-agent ask "inspect this repository and explain the test layout"
chaos-agent resume <thread-id>
chaos-agent resume <thread-id> "continue the previous task"
chaos-agent run --json "list the relevant files"
```

The Windows UI appends completed user, agent, tool, diff, warning, and error
entries to the normal Windows Terminal buffer. Windows Terminal owns selection,
copying, and scrollback. The live tail is an input line plus one status line;
typing `/` filters its command palette. `/主题`, `/字形`, and `/颜色` change only
application rendering and never modify the terminal font or profile.

Skills use an explicit, context-only manifest at either
`%USERPROFILE%\.chaos-agent\skills\<id>` or
`<workspace>\.chaos-agent\skills\<id>`, containing `skill.toml` and
`SKILL.md`. Workspace Skills require explicit activation and cannot register
tools, execute scripts, call the network, or change policy. Configured MCP
servers currently support `/mcp 状态 [server]` inventory only; no server is
started and no MCP action is exposed until a separate policy bridge exists.

## Safety Defaults

- `CHAOS_APPROVAL_MODE=ask` is the default. Workspace reads run automatically; a single external file read requires TUI approval and external writes are denied.
- `plan` allows workspace reads only. `auto` remains compatible and requires approval for outside-workspace access. `elevated` requires approval for each external read, write, or recursive enumeration. `full-local` permits typed external file operations, while commands still require approval.
- `allow_sensitive_paths = true` (or `CHAOS_ALLOW_SENSITIVE_PATHS=true`) is a separate explicit opt-in for `.env` files and private-key names. `.git`, `.code-agent`, and symlink/reparse paths remain protected at every level.
- Unknown and critical actions are denied. Destructive commands and unbounded output are rejected.
- The local runtime is controlled process execution, not an OS-level sandbox. Docker is optional and uses no network and no image pulls.

Sessions are stored at `%LOCALAPPDATA%\chaos-agent\sessions.sqlite3` by default. An existing legacy session database is reused until a new data directory is created.

## Foreground Tasks

Engineering requests in the Windows TUI run as durable foreground tasks. A task
is scoped to its current workspace and may perform ordinary workspace edits and
non-network local tests without a prompt for every action. Network access,
paths outside the workspace, unknown tools, and critical commands remain
blocked or require an explicit decision.

Use `Esc` or `/暂停 <task-id>` to pause, `/继续 <task-id>` to resume, `/停止
<task-id>` to stop, `/任务` to inspect tasks, and `/引导 <text>` to queue
guidance. Process commands are `chaos-agent task list` and `chaos-agent task resume
<task-id> [instruction]`. Chinese Windows uses Chinese task chrome by default;
`/language en` selects English UI labels. Paths, commands, model names, Git
refs, and raw tool data are never translated.

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

## Development Status

The first release targets Windows 10/11. The core protocol and adapters are portable, while Linux/macOS end-to-end terminal/runtime support remains future work.

## License And Acknowledgements

Released under the [MIT License](LICENSE).

This project acknowledges the public design work of uv-agent, Aider, Cline, OpenCode, mini-swe-agent, OpenHands, and Goose. They are references for problem framing and user experience, not code sources for this repository.
