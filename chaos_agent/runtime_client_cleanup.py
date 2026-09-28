from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable


async def close_partial_client(client: object) -> None:
    """Best-effort close without replacing the partial-build failure."""

    try:
        result = _close_result(client)
        if result is not None:
            await result
    except BaseException:
        return


def schedule_partial_client_close(
    client: object, pending: set[asyncio.Task[None]]
) -> None:
    """Retire a partial client from a synchronous factory failure path."""

    try:
        result = _close_result(client)
    except BaseException:
        return
    if result is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_finish(result))
        return
    task = loop.create_task(_finish(result))
    pending.add(task)
    task.add_done_callback(pending.discard)


def _close_result(client: object) -> Awaitable[object] | None:
    close = getattr(client, "aclose", None)
    if not callable(close):
        return None
    result = close()
    return result if inspect.isawaitable(result) else None


async def _finish(result: Awaitable[object]) -> None:
    try:
        await result
    except BaseException:
        return
