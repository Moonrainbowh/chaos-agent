"""Local prompt estimates, independent of provider usage and window accounting."""
from dataclasses import replace

from .attachment_budget import message_tokens
from .tokens import estimate_tokens


def prompt_estimate(system_prompt, messages, rendered_tools):
    return (estimate_tokens(system_prompt) + estimate_tokens(rendered_tools)
            + sum(message_tokens(message) for message in messages))


def with_system_prompt(bundle, system_prompt):
    """Refresh an existing local estimate when a wrapper appends system content."""
    measurements = dict(bundle.measurements)
    if "prompt_estimated_tokens" in measurements:
        measurements["prompt_estimated_tokens"] += (
            estimate_tokens(system_prompt) - estimate_tokens(bundle.system_prompt)
        )
    return replace(bundle, system_prompt=system_prompt, measurements=measurements)
