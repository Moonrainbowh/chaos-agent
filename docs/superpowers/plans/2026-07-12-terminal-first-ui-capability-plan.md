# Terminal-First UI And Capability Roadmap

## Status

Proposed implementation plan. This document supersedes the UI direction in the
root `plan.md` for future terminal work; it does not delete or rewrite that
historical plan.

## Product Decision

Default to a terminal-first coding-agent experience:

- Completed conversation and tool summaries append to the normal Windows
  Terminal buffer.
- Windows Terminal owns text selection, copying, and mouse-wheel scrollback.
- There is no fixed top header, summary panel, or shortcut bar.
- The live tail has an input line followed by one compact status line.
- The default visual profile is `signal`: ASCII glyphs, semantic ANSI colors,
  and the terminal's existing font. `Cascadia Mono` is a recommended Windows
  Terminal profile font, but the application never changes fonts itself.
- Unicode glyphs and alternate visual themes are optional user preferences.

The command surface grows in stages: reliable existing commands first, then
model/profile selection, then context-only Skills, then MCP connections.

## Scope

### In Scope

- Replace the alternate-screen, whole-frame TUI with an append-only terminal
  transcript and a minimal redrawable live tail.
- Add typed display entries and trusted semantic color spans.
- Add a filterable slash-command palette and repair the existing command
  error, language, and diff paths.
- Add model/profile selection at an idle or next-task boundary.
- Add a trusted, context-only Skill registry and activation state.
- Add configured-server MCP status and controlled activation after a policy
  bridge exists.

### Out Of Scope

- Changing the user's Windows Terminal font, palette, or copy-on-select
  setting from the application.
- A default full-screen dashboard or app-owned mouse selection.
- Direct `/run`, `/write`, raw shell, raw API-key, or arbitrary configuration
  editing commands.
- Installing Skills or MCP servers from the TUI.
- Switching provider/model in the middle of an active stream or task.
- Background daemon execution, worktrees, automatic commits, or pushes.

## Constraints

- Preserve the central `ActionPolicy` boundary. UI commands do not execute
  tools, mutate configuration, or invoke providers directly.
- Keep terminal text safe: strip model-supplied control sequences and generate
  ANSI styles only from local trusted event types.
- Every important color has a distinct glyph/text fallback for `NO_COLOR` or
  `color=never`.
- Keep Python source and test files under the repository's 300-line limit.
  Split renderer, display model, input handling, and command control rather
  than growing `windows_tui.py` further.
- Preserve current user changes and the existing untracked planning documents.

## Target Interaction

```text
> repair the retry logic
* I found the retry branch in providers/transport.py.
: read  src/code_agent/providers/transport.py
+ modified  src/code_agent/providers/transport.py
> _
. code-fast · 2 skills · 0 MCP · running · 00:42
```

The first four rows are ordinary terminal scrollback. Only the final input and
status rows are live-tail state. When work completes, its final status is
appended as a normal entry and the footer returns to idle.

## Visual Contract

### Default `signal` Profile

| Entry | Marker | Color role |
| --- | --- | --- |
| User input | `>` | cyan |
| Agent response | `*` | terminal foreground |
| Tool activity | `:` | blue |
| File path | inline | teal |
| Command | `$` / inline | magenta |
| Success or change | `+` | green |
| Warning or approval | `!` | yellow |
| Error | `x` | red |
| Diff | `+` / `-` | green / red |
| Metadata | `.` | dim foreground |

### Optional Preferences

- `/theme signal|symbol|plain`
- `/glyphs ascii|unicode`
- `/color auto|always|never`

`symbol` may use Unicode markers such as `›`, `◆`, `↳`, `✓`, and `×`.
`plain` uses bracketed ASCII log markers. Font selection remains external to
the agent and is documented through an optional Windows Terminal profile
snippet during integration.

## Command Taxonomy

The palette appears only when the input starts with `/`; typing filters it.
Commands use Chinese labels with stable English aliases.

| Group | First commands | Rule |
| --- | --- | --- |
| Help and display | `/帮助`, `/状态`, `/主题`, `/颜色`, `/字形`, `/语言`, `/诊断`, `/退出` | Local UI state only |
| Tasks | `/任务`, `/暂停 [id]`, `/继续 [id]`, `/停止 [id]`, `/引导 <text>` | Delegate to `ForegroundTaskController` |
| Sessions | `/会话`, `/打开 <thread-id>` | Read through session services; no destructive actions initially |
| Workspace | `/差异 [path...]`, `/工具`, `/上下文` | Read-only data through injected services and policy |
| Models | `/模型`, `/模型 列表`, `/模型 使用 <profile>` | Only configured profiles; idle or next-task boundary |
| Skills | `/技能`, `/技能 信息 <id>`, `/技能 启用|禁用 <id>` | Context-only trusted registry |
| MCP | `/mcp`, `/mcp 状态 [server]`, later `/mcp 启用 <server>` | Configured servers only; policy-gated tool bridge |

No command creates a second safety model. Invalid command text produces an
in-band error entry and leaves the terminal session running.

## Delivery Plan

### Phase 0: Re-contract And Baseline

**Goal:** Replace the old fixed-header/app-scroll model in feature contracts
before changing code.

- Update the objective and boundary sections of
  `src/code_agent/interfaces/AGENTS.md` for normal-buffer transcript,
  native terminal scrollback, live-tail footer, typed display semantics, and
  slash-palette ownership.
- Add or update provider/config Feature contracts only for model selection
  boundaries; do not add Units until their implementation phase.
- Record the current defects as acceptance cases: alternate screen/mouse
  capture, flat transcript strings, unhandled slash errors, disconnected
  `/差异`, incomplete `/语言`, and stale model-profile documentation.
- Confirm the selected default is `signal` and font ownership remains with
  Windows Terminal.

**Exit gate:** Contract review shows no UI path bypasses policy/provider/tool
boundaries, and each Feature stays below ten planned Units.

### Phase 1: Terminal-First Transcript

**Goal:** Deliver native selection and wheel behavior with a compact bottom
input/status tail.

**Implementation units:**

1. `DisplayEntry` / `DisplaySpan` model for local semantic transcript data.
2. Safe event-to-display projection for model, task, tool, file, command,
   diff, success, warning, and error states.
3. ANSI style/theme resolver with `auto`, `always`, and `never` color modes.
4. Display-width-aware clipping and wrapping for CJK text and fallback fonts.
5. Append-only terminal renderer that never emits full-screen clear or mouse
   tracking sequences.
6. Minimal live-tail renderer for input plus the status row below it.

**Expected file direction:** split the current renderer/input concerns into
small `interfaces` modules rather than extending `windows_tui.py`.

**Tests:**

- Unit-test semantic entry rendering with color disabled and enabled.
- Verify model text cannot inject terminal control sequences.
- Verify display-width clipping for CJK and ASCII mix.
- Verify completed entries are append-only and only the live tail redraws.
- Manual Windows Terminal smoke test: drag-select output, copy it, wheel to
  prior output, submit another prompt, and verify no fixed top chrome exists.

**Exit gate:** Native selection and wheel work in Windows Terminal without
Shift modifier or application mouse capture.

### Phase 2: Reliable Palette And Existing Commands

**Goal:** Make command handling safe, discoverable, and truthful before
adding new capabilities.

- Replace exception-driven slash parsing with structured parse outcomes.
- Render an input-local, filterable palette above the composer.
- Repair `/差异` by projecting write diff metadata from completed action
  results, and route explicit diff requests through the existing read-only
  Git service.
- Repair `/语言` by passing the active catalog to the renderer.
- Allow current-task shorthand for pause/resume/stop where safe.
- Add `/状态`, `/会话`, `/打开`, `/工具`, `/上下文`, `/诊断`, visual preference
  commands, and user-visible error entries.
- Keep direct shell, raw write, and policy mutation commands absent.

**Tests:** parser recovery, palette filtering, command-to-controller routing,
language rendering, action-completed diff projection, session restoration,
and no TUI exit on malformed `/` input.

**Exit gate:** Every visible command either works end-to-end or is absent from
the palette; no command is a placeholder.

### Phase 3: Configured Model And Profile Selection

**Goal:** Let a user select a known model configuration without compromising
task accounting or credential boundaries.

- Replace the unused/stale model-profile path with a real typed profile
catalog backed by local provider configuration.
- Correct the README claim about unsupported `CODE_AGENT_MODEL_PROFILES` or
wire it fully; do not leave an advertised but inert configuration path.
- Add injected read-only profile-list and current-profile services for the UI.
- Add a controlled application/runtime switch path that creates an immutable
  provider configuration and client only at an idle or next-task boundary.
- Persist the actual selected profile/model in new task/checkpoint records;
  do not retain the generic `configured-model` label.
- Apply profile limits only after verifying the profile data is truly wired to
  context and task-budget enforcement.

**Commands:** `/模型`, `/模型 列表`, `/模型 使用 <profile>`.

**Tests:** configured list only, unknown profile rejection, no API-key
disclosure, no active-task switch, next-task persistence, model-specific task
audit record, and provider reconstruction preserving all typed settings.

**Exit gate:** A model switch is auditable, takes effect only at a safe
boundary, and cannot supply an arbitrary URL, protocol, or API key.

### Phase 4: Trusted Context-Only Skills

**Goal:** Add reusable instructions without granting new tools or implicit
permissions.

- Define a Skill manifest with identifier, description, source, version/hash,
  bounded instruction content, and trust scope.
- Define ordered discovery roots for user and workspace Skills. Workspace
  Skills are visible but require explicit activation unless trusted by policy.
- Add a session-scoped activation registry and bounded context insertion.
- Record active skill identifiers and hashes in session/task facts, not full
  secret-bearing content.
- Keep Skill v1 context-only: no embedded executable scripts, network calls,
  tool registration, or permission changes.

**Commands:** `/技能`, `/技能 信息 <id>`, `/技能 启用 <id>`, `/技能 禁用 <id>`.

**Tests:** malformed manifest rejection, duplicate/conflicting IDs, context
budget enforcement, trust prompts, activation persistence, and no new tool
definition from a Skill.

**Exit gate:** A Skill's provenance, active state, and context cost are visible
and it cannot alter the policy boundary.

### Phase 5: Configured MCP Servers

**Goal:** Connect preconfigured MCP servers through the same typed tool and
policy model as built-in actions.

- Define an MCP server registry with name, transport, command/endpoint
reference, enabled state, and explicit trust/approval state.
- Add bounded lifecycle management, health checks, reconnect handling, and
tool-schema validation.
- Namespace exposed tools by server and map each to a capability/risk class.
- Route every MCP action through `ActionPolicy`; unknown, network, critical,
or outside-workspace operations retain the existing restrictions.
- Start with list/status only, then enable/disable already configured servers.
  Installation, arbitrary server commands, marketplace access, and login flows
  remain outside the TUI.

**Commands:** `/mcp`, `/mcp 状态 [server]`, then `/mcp 启用 <server>` and
`/mcp 禁用 <server>`.

**Tests:** unavailable server, invalid schema, tool namespace collision,
server crash/reconnect, approval forwarding, and denial of an unknown MCP
tool.

**Exit gate:** MCP availability cannot silently expand the agent's authority.

### Phase 6: Integration And Release Verification

**Goal:** Wire completed Feature APIs into the Windows application and give
users accurate documentation.

- Update `code_agent_win` composition only after Feature contracts and tests
  are complete.
- Update CLI help and README with the terminal-first behavior, palette,
  safe model boundary, Skill trust model, MCP status semantics, and optional
  Windows Terminal profile snippet.
- Preserve `agent ask`, `agent resume`, and `agent run --json` behavior.
- Validate package build and editable-install smoke paths.

**Verification matrix:**

| Area | Required evidence |
| --- | --- |
| Interfaces | Feature unittest discovery passes |
| Providers/config | Feature unittest discovery passes, including profile switching |
| Skills | Registry/context tests pass |
| MCP | Registry/policy/transport tests pass without a real external server |
| Root integration | `python -m unittest discover -s tests -p 'test_*.py'` passes |
| Packaging | Wheel and source distribution build; installed entry point starts |
| Windows Terminal | Manual native selection, copying, scrollback, colors, no-color, palette, model-boundary smoke checks |

## Review Gates

1. Review Phase 0 contracts before implementation.
2. Review a Windows Terminal screenshot/manual smoke result after Phase 1.
3. Review model selection behavior before any configuration persistence.
4. Review Skill manifest/trust format before loading third-party content.
5. Review MCP threat model and server configuration schema before process or
   network connection work.

## Completion Definition

The release is complete when Chaos Agent defaults to an append-only,
selectable, scrollable Windows Terminal transcript; the input has only a
bottom status line; the palette exposes only functioning, policy-safe
commands; configured model switches are auditable at safe boundaries; and
Skills and MCP are added only after their independent trust and policy
contracts have passed review.
