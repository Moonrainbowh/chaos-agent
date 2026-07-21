from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from code_agent.core.cancellation import CancellationError, CancellationToken

from .events import DeclarativeEventRouter, EventProjection, PluginProposal
from .models import UiPrimitive
from .registry import PluginHost


class PluginInteractionHost(Protocol):
    async def notify(self, proposal: PluginProposal) -> object: ...

    async def interact(
        self, proposal: PluginProposal, cancellation: CancellationToken
    ) -> object: ...


class PluginActionExecutor(Protocol):
    async def execute(
        self, proposal: PluginProposal, cancellation: CancellationToken
    ) -> object: ...


@dataclass(frozen=True)
class ProposalOutcome:
    plugin_id: str
    subscription_id: str
    ok: bool
    code: str
    result: object | None = None


class PluginEventRuntime:
    """Execute still-active proposals only through Host-owned adapters."""

    def __init__(
        self,
        host: PluginHost,
        router: DeclarativeEventRouter,
        interactions: PluginInteractionHost,
        actions: PluginActionExecutor,
    ) -> None:
        self._host = host
        self._router = router
        self._interactions = interactions
        self._actions = actions

    async def handle(
        self, event: EventProjection, cancellation: CancellationToken
    ) -> tuple[ProposalOutcome, ...]:
        return tuple(
            [
                await self.execute(proposal, cancellation)
                for proposal in self._router.route(event)
            ]
        )

    async def execute(
        self, proposal: PluginProposal, cancellation: CancellationToken
    ) -> ProposalOutcome:
        if not isinstance(proposal, PluginProposal):
            raise TypeError("proposal must be PluginProposal")
        if not self._host.is_active(
            proposal.plugin_id, proposal.plugin_digest, proposal.generation
        ):
            return _outcome(proposal, False, "plugin_inactive")
        try:
            cancellation.raise_if_cancelled()
            if proposal.action is not None:
                result = await self._actions.execute(proposal, cancellation)
            elif (
                proposal.ui is not None
                and proposal.ui.primitive is UiPrimitive.NOTIFY
            ):
                result = await self._interactions.notify(proposal)
            elif proposal.ui is not None:
                result = await self._interactions.interact(
                    proposal, cancellation
                )
            else:
                return _outcome(proposal, False, "invalid_proposal")
            cancellation.raise_if_cancelled()
            return _outcome(proposal, True, "ok", result)
        except CancellationError:
            raise
        except Exception as error:
            return _outcome(
                proposal, False, f"host_{type(error).__name__.casefold()}"
            )


def _outcome(
    proposal: PluginProposal,
    ok: bool,
    code: str,
    result: object | None = None,
) -> ProposalOutcome:
    return ProposalOutcome(
        proposal.plugin_id, proposal.subscription_id, ok, code, result
    )
