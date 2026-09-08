from .history import RestoredThread
from code_agent.sessions.models import GoalStatus

def _summary_lines(history: RestoredThread, status: str) -> list[str]:
    active_goal = next(
        (goal.objective for goal in history.goals if goal.status is GoalStatus.ACTIVE),
        None,
    )
    fallback_goal = next(
        (message.content for message in history.messages if message.role == "user"),
        None,
    )
    summary: list[str] = []
    objective = active_goal if active_goal is not None else fallback_goal
    if objective is not None:
        summary.append("goal: " + _visible_text(objective))
    summary.append("status: " + status)
    if history.checkpoints:
        summary.append("checkpoint: " + history.checkpoints[-1].label)
    return summary


def _visible_text(value: str) -> str:
    return " ".join(value.split())[:120]
