from __future__ import annotations

from code_agent.core.task_state import TaskState

from .tokens import estimate_tokens, truncate_to_tokens


def render_task_state(state: TaskState, token_budget: int) -> str:
    """Render durable task facts in deterministic priority order."""
    if not isinstance(state, TaskState):
        raise TypeError("state must be a TaskState")
    if isinstance(token_budget, bool) or not isinstance(token_budget, int):
        raise TypeError("token_budget must be an integer")
    if token_budget < 0:
        raise ValueError("token_budget must not be negative")
    if state == TaskState.empty() or token_budget == 0:
        return ""
    heading = "Task state (facts are verified; working notes are unverified):"
    rendered = _fit("", heading, token_budget)
    for section in _sections(state):
        rendered = _fit(rendered, section, token_budget)
    return rendered


def _sections(state: TaskState) -> tuple[str, ...]:
    high = []
    if state.objective:
        high.append(f"Objective: {state.objective}")
    if state.subject_hash:
        high.append(f"Current verification subject: generation {state.code_generation} ({state.subject_hash[:16]})")
    high.extend(
        f"Failed command: {fact.command} (exit {fact.returncode}): {fact.reason}"
        for fact in state.failed_commands
    )
    high.extend(f"Changed file: {path}" for path in state.files_changed)
    if state.open_questions:
        high.append("Open questions:\n" + "\n".join(f"- {item}" for item in state.open_questions))
    low = []
    if state.verified_facts:
        low.append("Verified facts:\n" + "\n".join(f"- {item}" for item in state.verified_facts))
    if state.working_notes:
        low.append("Working notes (unverified):\n" + "\n".join(f"- {item}" for item in state.working_notes))
    if state.files_read:
        low.append("Files read:\n" + "\n".join(f"- {item}" for item in state.files_read))
    return tuple(high + low)


def _fit(current: str, addition: str, budget: int) -> str:
    candidate = addition if not current else f"{current}\n{addition}"
    if estimate_tokens(candidate) <= budget:
        return candidate
    if not current:
        return truncate_to_tokens(addition, budget)
    return current
