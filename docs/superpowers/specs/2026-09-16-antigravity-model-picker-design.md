# Antigravity Model Picker Design

## Goal

Keep `/model` as the only model-selection command while preventing the complete
Antigravity OAuth catalog from crowding its first-level picker.

## User Flow

1. `/model` shows configured profiles, saved-login models, and one enabled
   `antigravity:oauth` entry when that credential is saved.
2. Selecting that entry opens `/model antigravity:oauth ` without changing the
   active runtime or creating a conversation.
3. The second-level picker shows every Antigravity catalog model. Existing text
   filtering, arrow-key navigation, Tab completion, Esc, and Enter selection
   behavior apply unchanged.
4. Selecting a model registers its existing ephemeral saved-login profile,
   switches runtime at the normal idle boundary, creates a new conversation on
   an actual change, and persists the selection through the existing preference
   store.

## Boundaries

- Reuse the local `ModelCatalog`; this flow performs no Antigravity network
  request and does not persist a copied model catalog.
- Do not add a second public command or restore `/logswitch`.
- WorkBuddy remains account-discovered and keeps its current refresh behavior.
- Missing credentials, invalid catalog models, busy tasks, and failed runtime
  construction retain the current runtime and use existing in-band errors.

## Implementation Shape

- Generalize the saved-login loading choice in `AuthenticationRuntimeControl`
  so Antigravity and WorkBuddy can expose a compact provider/auth entry.
- Route the Antigravity load choice in `/model` to populate the command input
  with `antigravity:oauth `; unlike WorkBuddy, this is synchronous local catalog
  expansion rather than async discovery.
- Add focused tests for first-level compactness, second-level full-catalog
  filtering and selection, and no runtime change while opening the second level.

## Verification

Run focused model-picker/auth-control tests, the full Interfaces suite, the
Authentication suite, compilation, and `git diff --check`.
