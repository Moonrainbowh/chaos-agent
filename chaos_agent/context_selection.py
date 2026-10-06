"""Non-sensitive context facts frozen into new tasks and checked before resume."""
import json
from dataclasses import asdict

from code_agent.core._json import plain


def context_selection_facts(profile, mode):
    """Use the same Host prompt budget and actual profile policy as composition."""
    from chaos_agent.application_context import _profile_prompt_budget
    budget = _profile_prompt_budget(profile, mode)
    policy = profile.context_policy
    return {"version": 1, "strategy": "semantic" if policy is None else policy.strategy,
            "policy": None if policy is None else asdict(policy),
            "context_window": profile.context_window, "api_input_tokens": profile.api_input_tokens,
            "max_output_tokens": profile.max_output_tokens,
            "host_prompt_tokens": budget.max_prompt_tokens,
            "safety_tokens": budget.safety_tokens if policy is None else policy.safety_tokens,
            "counter_version": "prepared-json-v1"}


def context_selection_json(profile, mode):
    return json.dumps(context_selection_facts(profile, mode), sort_keys=True, separators=(",", ":"))


def require_context_selection(contract, profile, mode):
    """Old contracts retain their configured compatibility path; never invent facts."""
    if contract.context_selection is not None and plain(contract.context_selection) != context_selection_facts(profile, mode):
        raise RuntimeError("recorded context selection no longer matches configuration; decision required")
