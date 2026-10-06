# Chaos Agent

A terminal coding agent with durable tasks, policy-gated tools, CLI/JSON output, an ACP editor adapter and a paired phone interface. Task status and verification come from recorded execution facts.

## Install

Python 3.10 or newer is required. From a source checkout:

```sh
python -m pip install .
chaos-agent --help
```

`agent` remains compatible. Windows commands require PowerShell 7 (`pwsh`); Linux and macOS use `/bin/sh`. The runtime is not an OS sandbox. Windows uses Job Objects and POSIX uses process groups. Manual macOS end-to-end acceptance remains separate from automated tests.

## Configure a model

Open `chaos-agent`, use `/login` to authenticate and `/model` to select a model. Credentials use hidden input. You can also configure an account before opening a workspace:

```sh
chaos-agent auth providers
chaos-agent auth login openai-codex --method browser
chaos-agent auth models openai-codex
chaos-agent auth configure openai-codex <model-id> --profile codex-account
chaos-agent --profile codex-account
```

For a custom API, create the platform-local `chaos-agent/config.toml` at the path reported by a configuration error:

```toml
[default]
provider = "custom"

[providers.custom]
api = "responses"
base_url = "https://your-provider.example/v1"
model = "your-model-id"
api_key_env = "YOUR_PROVIDER_KEY"
context_window = 128000
max_output_tokens = 16384
```

Set the named key environment variable outside the project. Declare actual model limits; the example numbers do not prove provider capabilities. Do not commit API keys. See the [provider login guide](docs/provider-login.md) for supported login methods.

The default flow selects its tool and context strategies. Model selection, reasoning effort, Ask/Code intent and permission remain independent. Advanced settings, environment aliases and the retained four-preset `--mode` option live in one [advanced reference](docs/advanced-configuration.md).

## Run a task

```sh
chaos-agent
chaos-agent ask "Explain the request routing in this project"
chaos-agent run --json "Fix the parser and run the relevant tests"
```

The interactive UI uses Auto intent, freezing read-only requests as Ask and modification requests as Code. `/model`, `/effort` and `/permission` control separate settings. `/help` lists commands, `/evidence` shows verification and `/memory` lets you inspect or withdraw project memory. [Conversation controls](docs/pi-conversation-controls.md) cover branching, history and `!` commands.

## Resume and inspect

```sh
chaos-agent task list
chaos-agent task result <task-id>
chaos-agent task resume <task-id>
chaos-agent resume <thread-id>
chaos-agent history <thread-id>
chaos-agent task recovery <task-id>
```

History and task queries read persisted state without initializing a model or command runtime. An unresolved action stays blocked until explicitly reconciled; recovery does not replay an unknown write. Use `chaos-agent task --help` for reconciliation arguments. Partial delivery or an unverified result does not imply successful validation.

## Permission and remote access

Default `auto` permission permits ordinary operations within the selected workspace; network access, outside paths and protected actions retain policy checks. `ask` requires write/command approval. `unrestricted` is an explicit high-trust setting. Typed file tools protect `.git`, credentials and symlink/reparse paths. Sensitive files need a separate opt-in; approved shell commands run with your account's access.

For a phone, use a paired HTTPS/WSS entry backed by `chaos-agent host`, or the [phone SSH guide](docs/mobile-ssh.md). The Host defaults to loopback; a LAN listener requires TLS. Pairing and action approval do not grant permanent permission. `chaos-agent-acp` exposes the shared task service over ACP stdio for editor clients.

## Development and evaluation

```sh
python -m pip install -e .
python scripts/run_tests.py
python -m build
```

The standard runner discovers all Feature suites, phone backend tests and root integration tests. CI uses Windows Python 3.10/3.13, Ubuntu 3.10/3.13 and macOS 3.13. A configured matrix does not prove a particular candidate has passed it.

Development evaluation is excluded from the runtime wheel and provided separately; see [benchmarks](benchmarks/README.md) for build, installation and offline checks. Source scripts and historical imports remain compatible. Offline fixtures do not measure real model success.

Provider adapters include MIT-licensed work adapted from URI Agent; see [third-party notices](THIRD_PARTY_NOTICES.md). Released under the [MIT License](LICENSE).
