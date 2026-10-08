"""Read durable task facts without constructing the execution application."""
from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from code_agent.interfaces.history import load_thread_history
from code_agent.interfaces.task_controller import ForegroundTaskController
from code_agent.sessions.repository import SQLiteSessionRepository


def is_history_query(arguments: Sequence[str]) -> bool:
    values = tuple(arguments)
    return (len(values) == 2 and values[0] == "history" and bool(values[1].strip())) or values == ("task", "list") or (
        len(values) == 3 and values[:2] in {
            ("task", "result"), ("task", "recovery")
        } and bool(values[2].strip())
    )


async def run_history_query(
    arguments: Sequence[str], write: Callable[[str], object], *,
    database_path: Path | str | None = None,
) -> int:
    """Use normal repository validation and the shared result projection.

    The optional database path is a composition/test seam, not a CLI override.
    Opening follows the same schema and legacy migration path as application
    startup; querying never reconciles actions or starts/resumes a task.
    """
    if not is_history_query(arguments):
        raise ValueError("not a read-only task query")
    sessions = None
    try:
        if database_path is None:
            from .app_paths import session_path
            database_path = await asyncio.to_thread(session_path)
        sessions = await asyncio.to_thread(SQLiteSessionRepository, database_path)
        if arguments[0] == "history":
            history = await load_thread_history(sessions, arguments[1])
            data = {"version": 1, "thread_id": history.thread_id,
                    "messages": [message.to_dict() for message in history.messages],
                    "events": [event.to_dict() for event in history.events],
                    "message_count": history.message_count, "event_count": history.event_count,
                    "truncated": history.truncated}
            write(json.dumps(data, ensure_ascii=False) + "\n")
            return 0
        tasks = ForegroundTaskController(None, sessions, Path.cwd())
        if arguments[1] == "list":
            for task in await tasks.list(include_terminal=True):
                write(f"{task.id} {task.status.value} {task.contract.objective[:120]}\n")
        elif arguments[1] == "result":
            result = await tasks.result(arguments[2])
            write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
        else:
            data = await tasks.recovery_checklist(arguments[2])
            write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")
        return 0
    except Exception as error:
        # Repository errors may contain SQL, paths or persisted user content.
        print(f"history error: {type(error).__name__}", file=sys.stderr)
        return 1
    finally:
        if sessions is not None:
            sessions.close()
