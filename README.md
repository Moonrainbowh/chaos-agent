# code-agent-win

`code-agent-win` is a Windows-first, open-source coding agent with a shared headless core, a full-screen terminal UI, and non-interactive CLI/JSON modes.

It is a clean-room implementation. It takes architectural lessons from projects such as uv-agent, Aider, Cline, OpenCode, mini-swe-agent, OpenHands, and Goose, but does not copy their source code.

## What It Does

- Uses OpenAI Responses, OpenAI Chat Completions, or Anthropic Messages through one streaming model protocol.
- Keeps sessions, messages, events, goals, and checkpoints in a versioned SQLite database.
- Discovers hierarchical `AGENTS.md` rules, builds a bounded repository map, and compacts history deterministically.
- Routes file reads, edits, Git inspection, and PowerShell commands through typed tools, central policy checks, audit events, and explicit approval.
- Provides a Windows Terminal TUI (`agent`), a text CLI (`agent ask`), session resume (`agent resume`), and machine-readable events (`agent run --json`).

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

Create `%LOCALAPPDATA%\code-agent\config.toml` to configure a provider once for the current Windows user:

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

Multiple `[providers.<name>]` profiles are supported. Use `agent --profile <name>` or `CODE_AGENT_PROFILE` to choose one; `CODE_AGENT_CONFIG` may select another absolute config path. Existing `CODE_AGENT_API`, `CODE_AGENT_BASE_URL`, `CODE_AGENT_MODEL`, `CODE_AGENT_API_KEY_ENV`, and `CODE_AGENT_APPROVAL_MODE` override the selected profile. A profile may use either `api_key` or `api_key_env`, not both.

The API key is never written to a session or project file. The local file is suitable only for a personal Windows user: another process running as the same user can theoretically read it after explicit approval. Windows Credential Manager is a possible future enhancement, not a current dependency. The file tool rejects the local configuration directory even when it is used as a workspace.

The environment-only flow remains supported:

```powershell
$env:OPENAI_API_KEY = "..."
$env:CODE_AGENT_API = "responses"
$env:CODE_AGENT_BASE_URL = "https://api.openai.com"
$env:CODE_AGENT_MODEL = "gpt-4.1-mini"
```

For named model configurations, set `CODE_AGENT_MODEL_PROFILES` to one JSON
object. Every profile declares its model limits, protocol and API-key variable;
the key value itself remains in its environment variable.

```powershell
$env:CODE_AGENT_MODEL_PROFILES = '{"fast":{"model":"gpt-4.1-mini","api":"responses","api_key_env":"OPENAI_API_KEY","context_window":128000,"max_output_tokens":16384,"max_agent_rounds":50,"max_tool_calls":128,"max_tool_calls_per_round":50}}'
$env:CODE_AGENT_DEFAULT_MODEL = "fast"
```

Provider selection values:

| `CODE_AGENT_API` | Protocol |
| --- | --- |
| `responses` | OpenAI Responses API |
| `chat_completions` | OpenAI-compatible Chat Completions |
| `anthropic_messages` | Anthropic Messages API |

Use `CODE_AGENT_API_KEY_ENV` when the key variable is not `OPENAI_API_KEY`.

## Run

```powershell
agent
agent --model fast ask "inspect this repository"
agent ask "inspect this repository and explain the test layout"
agent resume <thread-id>
agent resume <thread-id> "continue the previous task"
agent run --json "list the relevant files"
```

The Windows TUI supports typing, streaming transcript updates, and a tool timeline. Resuming or selecting a session restores a compact task summary and recent actions; `PageUp`/`PageDown` browse chat history, while `Home` and `End` jump to its oldest and newest visible positions. `D` retains Diff preview, `S` retains recent session selection, and `Y`/`N` retain approval for writes and commands. It is designed for Windows Terminal and PowerShell.

## Safety Defaults

- `CODE_AGENT_APPROVAL_MODE=ask` is the default. Workspace reads run automatically; a single external file read requires TUI approval and external writes are denied.
- `plan` allows workspace reads only. `auto` remains compatible and requires approval for outside-workspace access. `elevated` requires approval for each external read, write, or recursive enumeration. `full-local` permits typed external file operations, while commands still require approval.
- `allow_sensitive_paths = true` (or `CODE_AGENT_ALLOW_SENSITIVE_PATHS=true`) is a separate explicit opt-in for `.env` files and private-key names. `.git`, `.code-agent`, and symlink/reparse paths remain protected at every level.
- Unknown and critical actions are denied. Destructive commands and unbounded output are rejected.
- The local runtime is controlled process execution, not an OS-level sandbox. Docker is optional and uses no network and no image pulls.

Sessions are stored at `%LOCALAPPDATA%\code-agent\sessions.sqlite3` by default.

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
