from __future__ import annotations


async def thread_is_task_owned(app: object) -> bool:
    thread_id = getattr(app, "current_thread_id", None)
    sessions = getattr(app, "sessions", None)
    list_tasks = getattr(sessions, "list_tasks", None)
    if thread_id is None or not callable(list_tasks):
        return False
    try:
        tasks = await list_tasks(include_terminal=False)
    except TypeError:
        tasks = await list_tasks()
    return any(getattr(task, "thread_id", None) == thread_id for task in tasks)


def peer_preview(value: str, limit: int = 400) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"
