"""Opt-in managed context settings; absent settings preserve legacy behavior."""
from code_agent.context_windows.policy import WindowPolicy


def configured_context_policy(values):
    """Resolve only trusted local profile metadata; explicit settings always win."""
    if "context_policy" not in values:
        metadata = values.get("model_metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("model_metadata must be a table")
        defaults = metadata.get("token_budget", {})
        if not isinstance(defaults, dict):
            raise ValueError("model_metadata.token_budget must be a table")
        enabled = defaults.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("model metadata token budget enabled must be boolean")
        if not enabled:
            return None
        allowed = set(WindowPolicy.__dataclass_fields__) - {"strategy"}
        settings = {key: value for key, value in defaults.items() if key in allowed}
        return WindowPolicy(strategy="persistent", **settings)
    raw = values.get("context_policy")
    if raw is None or raw is False:
        return None
    if not isinstance(raw, dict):
        raise ValueError("context_policy must be a table")
    allowed = set(WindowPolicy.__dataclass_fields__)
    if set(raw) - allowed:
        raise ValueError("unknown context_policy setting")
    return WindowPolicy(**raw)
