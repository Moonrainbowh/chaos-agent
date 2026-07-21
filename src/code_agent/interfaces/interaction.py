from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.plugins.events import PluginProposal
from code_agent.plugins.models import UiRequest


class InteractionPrimitive(str, Enum):
    NOTIFY = "notify"
    CONFIRM = "confirm"
    INPUT = "input"
    SELECT = "select"


@dataclass(frozen=True)
class HostInteraction:
    identifier: str
    primitive: InteractionPrimitive
    title: str
    prompt: str
    options: tuple[str, ...] = ()
    source: str = "host"
    risk: str | None = None
    target: str | None = None

    def __post_init__(self) -> None:
        for name, limit in (("identifier", 256), ("title", 160), ("prompt", 2_048), ("source", 256)):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f"{name} must be non-blank bounded text")
        if not isinstance(self.primitive, InteractionPrimitive):
            raise TypeError("primitive must be InteractionPrimitive")
        options = tuple(self.options)
        if len(options) > 20 or any(not isinstance(item, str) or not item.strip() for item in options):
            raise ValueError("options must be bounded labels")
        if self.primitive is InteractionPrimitive.SELECT and not options:
            raise ValueError("select requires options")
        object.__setattr__(self, "options", options)


@dataclass(frozen=True)
class InteractionResult:
    identifier: str
    accepted: bool
    value: str | None = None
    cancelled: bool = False


def render_interaction(interaction: HostInteraction, selected: int = 0) -> tuple[str, ...]:
    heading = f"{interaction.title} · {interaction.source}"
    details = [heading, interaction.prompt]
    if interaction.target:
        details.append("target: " + interaction.target)
    if interaction.risk:
        details.append("risk: " + interaction.risk)
    options = interaction.options
    if interaction.primitive is InteractionPrimitive.CONFIRM:
        options = ("No", "Yes")
    for index, option in enumerate(options):
        details.append(("› " if index == selected else "  ") + option)
    if interaction.primitive in {InteractionPrimitive.CONFIRM, InteractionPrimitive.SELECT}:
        details.append("Enter select · Esc cancel")
    return tuple(details)


class InteractionBroker:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[HostInteraction] = asyncio.Queue()
        self._waiters: dict[str, asyncio.Future[InteractionResult]] = {}

    async def request(self, interaction: HostInteraction, cancellation: CancellationToken) -> InteractionResult:
        if interaction.identifier in self._waiters:
            raise ValueError("interaction id is already pending")
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[InteractionResult] = loop.create_future()
        self._waiters[interaction.identifier] = waiter
        await self._queue.put(interaction)
        cancelled = asyncio.create_task(cancellation.wait_async())
        try:
            done, _ = await asyncio.wait((waiter, cancelled), return_when=asyncio.FIRST_COMPLETED)
            if waiter in done:
                return waiter.result()
            raise CancellationError(cancellation.reason or "cancelled")
        finally:
            self._waiters.pop(interaction.identifier, None)
            cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)

    async def next_request(self) -> HostInteraction:
        return await self._queue.get()

    def resolve(self, result: InteractionResult) -> bool:
        waiter = self._waiters.get(result.identifier)
        if waiter is None or waiter.done():
            return False
        waiter.set_result(result)
        return True


class PluginInteractionAdapter:
    """Route plugin UI through Host display or the shared InteractionBroker."""

    def __init__(
        self,
        broker: InteractionBroker,
        display: Callable[[HostInteraction], object],
    ) -> None:
        if not isinstance(broker, InteractionBroker):
            raise TypeError("broker must be InteractionBroker")
        if not callable(display):
            raise TypeError("display must be callable")
        self._broker = broker
        self._display = display

    async def notify(self, proposal: PluginProposal) -> HostInteraction:
        interaction = self._convert(proposal)
        if interaction.primitive is not InteractionPrimitive.NOTIFY:
            raise ValueError("notify requires a notify proposal")
        self._display(interaction)
        return interaction

    async def interact(
        self, proposal: PluginProposal, cancellation: CancellationToken
    ) -> InteractionResult:
        interaction = self._convert(proposal)
        if interaction.primitive is InteractionPrimitive.NOTIFY:
            raise ValueError("notify does not request a user answer")
        return await self._broker.request(interaction, cancellation)

    @staticmethod
    def _convert(proposal: PluginProposal) -> HostInteraction:
        if not isinstance(proposal, PluginProposal) or proposal.ui is None:
            raise TypeError("proposal must contain plugin UI")
        identifier = (
            f"plugin:{proposal.plugin_id}:{proposal.subscription_id}:"
            f"{proposal.generation}"
        )
        return plugin_interaction(
            proposal.ui, proposal.plugin_id, identifier
        )


def plugin_interaction(
    request: UiRequest, plugin_id: str, identifier: str
) -> HostInteraction:
    if not isinstance(request, UiRequest):
        raise TypeError("request must be a plugin UiRequest")
    return HostInteraction(
        identifier,
        InteractionPrimitive(request.primitive.value),
        request.title,
        request.prompt,
        request.options,
        source=f"plugin.{plugin_id}",
    )
