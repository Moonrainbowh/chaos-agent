from __future__ import annotations

import asyncio
from collections.abc import Sequence

from .interaction import InteractionResult


def start_animation(app: object) -> None:
    if app._animation_task is None or app._animation_task.done():
        app._animation_task = asyncio.create_task(animate(app))


async def stop_animation(app: object) -> None:
    if app._animation_task and not app._animation_task.done():
        app._animation_task.cancel()
    if app._animation_task:
        await asyncio.gather(app._animation_task, return_exceptions=True)
    app._animation_task = None


async def animate(app: object) -> None:
    while app._run_task and not app._run_task.done():
        app._spinner_index += 1
        if app.state.status == "running" or app.state.has_draft:
            app.redraw()
        await asyncio.sleep(1 / 30)


async def listen_approvals(app: object) -> None:
    while True:
        app._pending_approval = await app.approvals.next_request()
        app._approval_done.clear()
        app.redraw()
        await app._approval_done.wait()


async def listen_interactions(app: object) -> None:
    while app.interaction_broker is not None:
        app._pending_interaction = await app.interaction_broker.next_request()
        app._interaction_done.clear()
        app.redraw()
        await app._interaction_done.wait()


async def close_tasks(app: object) -> None:
    if app._pending_approval is not None:
        app.approvals.resolve(app._pending_approval.request_id, False)
        app._pending_approval = None
        app._approval_done.set()
    if app._pending_interaction is not None and app.interaction_broker:
        app.interaction_broker.resolve(
            InteractionResult(
                app._pending_interaction.identifier,
                False,
                cancelled=True,
            )
        )
        app._pending_interaction = None
        app._interaction_done.set()
    if app.tasks and app.active_task_id:
        await app.tasks.interrupt(app.active_task_id, "TUI closed")
    if app._token:
        app._token.cancel("TUI closed")
    tasks = (
        app._run_task,
        app._approval_task,
        app._interaction_task,
        app._animation_task,
    )
    for task in tasks:
        if task:
            task.cancel()
    await asyncio.gather(
        *(task for task in tasks if task), return_exceptions=True
    )


def format_command_help(specs: Sequence[object]) -> str:
    groups: dict[str, list[str]] = {}
    for spec in specs:
        groups.setdefault(spec.group, []).append(
            f"  {spec.display} · {spec.description}"
        )
    return "\n".join(
        line
        for group, commands in groups.items()
        for line in (group, *commands)
    )
