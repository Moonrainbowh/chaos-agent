from __future__ import annotations

import asyncio

from .terminal_display import DisplayKind


async def yield_peer_slot(app: object) -> bool:
    """Bound peer cancellation before a user submission may reuse the run slot."""
    peer_task = app._peer_run_task
    if peer_task is None or peer_task.done():
        return True
    if app._token is not None:
        app._token.cancel("user input takes precedence over peer delivery")
    done, _ = await asyncio.wait((peer_task,), timeout=0.25)
    if not done:
        peer_task.cancel()
        done, _ = await asyncio.wait((peer_task,), timeout=0.25)
    if not done:
        app._append(
            DisplayKind.ERROR,
            "peer delivery is still stopping; retry your input shortly",
        )
        app.redraw()
        return False
    await asyncio.gather(peer_task, return_exceptions=True)
    if app._run_task is peer_task:
        app._run_task = None
    app._peer_run_task = None
    return True
