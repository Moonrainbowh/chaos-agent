from __future__ import annotations

from code_agent.core.attachments import AttachmentRef
from code_agent.core.events import AgentEvent, EventKind

from .steering_view import SteeringKind, SteeringStage
from .terminal_display import DisplayKind


async def steer_input(
    interactions: object,
    app: object,
    task_id: str,
    instruction: str,
    attachments: tuple[AttachmentRef, ...] = (),
) -> None:
    item = interactions.steering.queue(
        instruction or "apply attached user input", kind=SteeringKind.STEER
    )
    if attachments:
        await app.tasks.steer(task_id, instruction, attachments=attachments)
    else:
        await app.tasks.steer(task_id, instruction)
    interactions.steering.transition(item.identifier, SteeringStage.STEERED)
    app._append(DisplayKind.METADATA, interactions.steering.status_line())


async def queue_followup(
    interactions: object,
    app: object,
    task_id: str,
    instruction: str,
    attachments: tuple[AttachmentRef, ...] = (),
) -> None:
    identifier = (
        await app.tasks.queue(task_id, instruction, attachments=attachments)
        if attachments
        else await app.tasks.queue(task_id, instruction)
    )
    interactions.steering.queue(
        instruction or "apply attached user input",
        identifier,
        kind=SteeringKind.QUEUE,
    )
    app._append(DisplayKind.METADATA, interactions.steering.status_line())


def observe_active_input(
    interactions: object, app: object, event: AgentEvent
) -> None:
    if event.kind is EventKind.TASK_FOLLOWUPS_PROMOTED:
        changed = _apply_promoted(interactions, event.payload.get("ids", ()))
    elif event.kind in {EventKind.TURN_STARTED, EventKind.CONTEXT_BUILT}:
        changed = _advance_steering(interactions, event.kind)
    else:
        return
    if changed:
        app._append(DisplayKind.METADATA, interactions.steering.status_line())


def _advance_steering(interactions: object, kind: EventKind) -> bool:
    source, target = (
        (SteeringStage.STEERED, SteeringStage.DEQUEUED)
        if kind is EventKind.TURN_STARTED
        else (SteeringStage.DEQUEUED, SteeringStage.APPLIED)
    )
    changed = False
    for item in interactions.steering.items:
        if item.kind is SteeringKind.STEER and item.stage is source:
            interactions.steering.transition(item.identifier, target)
            changed = True
    return changed


def _apply_promoted(interactions: object, identifiers: object) -> bool:
    values = identifiers if isinstance(identifiers, (tuple, list)) else ()
    changed = False
    for identifier in values:
        changed = interactions.steering.mark_applied(identifier) or changed
    return changed
