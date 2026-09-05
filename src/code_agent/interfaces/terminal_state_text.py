from __future__ import annotations

from code_agent.core.models import Message


def compact_response(value: str) -> str:
    result: list[str] = []
    for raw in value.splitlines():
        line = raw.rstrip()
        if line.strip() or (result and result[-1]):
            result.append(line)
    return "\n".join(result).strip()


def transcript_lines(messages: tuple[Message, ...]) -> list[str]:
    return [
        message.role + ": " + message.content
        for message in messages
        if message.role == "user" or (message.role == "assistant" and message.content)
    ]


def action_summary(actions: list[str], failed: list[str]) -> str:
    count = len(actions)
    return f"{count} {'action' if count == 1 else 'actions'} finished" + (f" · {len(failed)} failed" if failed else "")
