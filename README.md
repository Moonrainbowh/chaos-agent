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

The Windows TUI supports typing, streaming transcript updates, a tool timeline, `D` for Diff preview, `S` for recent session selection, and `Y`/`N` approval for writes and commands. It is designed for Windows Terminal and PowerShell.

## Safety Defaults

- `CODE_AGENT_APPROVAL_MODE=ask` is the default. Workspace reads run automatically; a single external file read requires TUI approval and external writes are denied.
- `plan` allows workspace reads only. `auto` remains compatible and requires approval for outside-workspace access. `elevated` requires approval for each external read, write, or recursive enumeration. `full-local` permits typed external file operations, while commands still require approval.
- `allow_sensitive_paths = true` (or `CODE_AGENT_ALLOW_SENSITIVE_PATHS=true`) is a separate explicit opt-in for `.env` files and private-key names. `.git`, `.code-agent`, and symlink/reparse paths remain protected at every level.
- Unknown and critical actions are denied. Destructive commands and unbounded output are rejected.
- The local runtime is controlled process execution, not an OS-level sandbox. Docker is optional and uses no network and no image pulls.

Sessions are stored at `%LOCALAPPDATA%\code-agent\sessions.sqlite3` by default.

## Development Status

The first release targets Windows 10/11. The core protocol and adapters are portable, while Linux/macOS end-to-end terminal/runtime support remains future work.

## License And Acknowledgements

Released under the [MIT License](LICENSE).

This project acknowledges the public design work of uv-agent, Aider, Cline, OpenCode, mini-swe-agent, OpenHands, and Goose. They are references for problem framing and user experience, not code sources for this repository.
