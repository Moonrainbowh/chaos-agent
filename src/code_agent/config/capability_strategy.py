from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from code_agent.capabilities import CapabilityStrategy


def configured_capability_strategy(
    document: Mapping[str, Any], env: Mapping[str, str]
) -> CapabilityStrategy:
    """Resolve the capability A/B strategy from agent config and environment."""
    agent = document.get("agent", {})
    if agent is not None and not isinstance(agent, dict):
        raise ValueError("agent must be a table")
    value = env.get(
        "CHAOS_CAPABILITY_STRATEGY",
        env.get(
            "CODE_AGENT_CAPABILITY_STRATEGY",
            agent.get("capability_strategy", CapabilityStrategy.HYBRID.value),
        ),
    )
    if not isinstance(value, str) or not value.strip():
        raise ValueError("capability_strategy must be non-empty text")
    try:
        return CapabilityStrategy(value)
    except ValueError:
        raise ValueError(
            "capability_strategy must be legacy, hybrid, or progressive"
        ) from None
