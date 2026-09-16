# Unified model selection design

## Goal

Make `/model` the only interactive model-selection entry point. It must show
configured profiles and models available through saved logins, including
WorkBuddy. Remember the last successful selection for the current Windows user
and restore it at the next interactive startup. Remove `/logswitch` entirely.

## Current problem

`/model` reads only the configured runtime profiles. `/logswitch` separately
reads saved credentials, creates an in-memory login profile, and is the only
place that can load WorkBuddy's account-bound model list. This exposes an
implementation distinction to users: a successful WorkBuddy login still
requires knowing a second, non-obvious command to choose a model.

WorkBuddy's model list is intentionally not persisted. It is account-bound and
only exists in memory after `/v3/config` discovery, so a restart currently
cannot recreate a selected WorkBuddy model.

## Selected approach

### One picker

- `/login` remains responsible only for credentials. A successful WorkBuddy
  login immediately performs one account-model discovery and then opens
  `/model`; if discovery fails, the login remains saved and `/model` shows a
  retryable WorkBuddy load item.
- `/model` merges configured profiles with saved-login candidates. Candidate
  labels distinguish their source, such as `Configured · <model>` and
  `WorkBuddy · OAuth · <model>`.
- A saved non-WorkBuddy catalog model and a configured profile both select
  through the existing idle-only runtime switch boundary. A WorkBuddy load or
  refresh item asynchronously fetches its account catalog, then refreshes the
  same `/model` picker rather than moving the user to another command.
- `/logswitch` is removed from parsing, registry entries, help, picker
  navigation, handlers, tests, and user-facing messages. It has no alias.

### Last-selection preference

Store a versioned, non-secret preference under the existing user product-state
directory. It records only the selected source kind, configured profile or
saved-login provider/authentication slot, and model ID. It never stores a
credential, authorization header, or a full WorkBuddy catalog.

Only a successful `/model` runtime switch writes the preference, using atomic
replacement. Corrupt or unsupported preference data is ignored safely. Explicit
CLI `--profile` or `--model` options always override it and do not rewrite it.
The preference is global to the current Windows user, not workspace-specific.

### Startup restoration

- A saved configured profile is restored before the interactive picker accepts
  input.
- For a saved WorkBuddy selection, interactive startup makes one bounded
  account-model discovery request before accepting input. If the saved model is
  still present, its temporary profile is recreated and selected through the
  same runtime switch path used by `/model`.
- Missing WorkBuddy credentials or a removed model clears the stale preference
  and starts with the configured default profile.
- A transient discovery or network failure preserves the preference for a
  future retry, starts with the configured default profile, and reports the
  failed restoration without exposing credentials.
- Failure to restore a preference never prevents startup and never silently
  substitutes a different WorkBuddy model.

## Boundaries

- The feature changes only interactive TUI selection and startup restoration.
  It does not modify the configured default profile, stored credential format,
  model catalog persistence rules, task-contract restoration, or noninteractive
  CLI semantics.
- A running task still blocks a model change. A successful change opens a new
  conversation; an unchanged or failed selection preserves the current one.
- WorkBuddy model discovery continues to use the credential-bound endpoint and
  existing safe timeout/redaction rules.

## Verification

- Verify `/logswitch` is no longer parsable or visible in command discovery.
- Verify `/model` contains configured profiles, saved-login models, and a
  WorkBuddy load/refresh candidate when its account models are absent.
- Verify WorkBuddy login loads models and returns to `/model`; a discovery
  failure preserves the login and provides a retry candidate.
- Verify successful configured and WorkBuddy selections write only the allowed
  preference fields, and malformed preference files fail closed.
- Verify startup restoration for a configured profile and a WorkBuddy profile;
  verify CLI overrides, missing credentials, missing models, and transient
  discovery errors use the specified fallback behavior.
- Run the affected authentication, provider/runtime, TUI command, and Windows
  integration tests, then the repository test runner.
