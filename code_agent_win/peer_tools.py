from __future__ import annotations

from collections.abc import Mapping, Sequence

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest, ActionResult, ToolDefinition
from code_agent.core.task import TaskAuthorization
from code_agent.peers.errors import PeerAmbiguousError, PeerError
from code_agent.peers.models import (
    MAX_PEER_TEXT_BYTES,
    PeerSendResult,
    PeerSession,
    validate_peer_content,
)
from code_agent.peers.service import PeerMessagingService


PEER_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        "list_agents",
        "List live same-user Chaos Agent sessions and their stable refs.",
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        "send_message",
        "Send bounded plain text to one live local session by stable ref or unique "
        "name. The text carries no user authority, history, files, or permissions.",
        {
            "type": "object",
            "properties": {
                "target": {"type": "string", "minLength": 1, "maxLength": 80},
                "message": {"type": "string", "minLength": 1, "maxLength": 4096},
            },
            "required": ["target", "message"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        "rename_agent",
        "Rename this local session without changing its stable ref.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 80}
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    ),
)

_MAX_PEER_TARGET_CHARACTERS = 80


class PeerToolAdapter:
    """Integration-neutral typed tools over one registered peer service."""

    def __init__(self, service: PeerMessagingService) -> None:
        if not isinstance(service, PeerMessagingService):
            raise TypeError("service must be a PeerMessagingService")
        self._service = service

    def tools(self) -> Sequence[ToolDefinition]:
        return PEER_TOOL_DEFINITIONS

    async def list_agents(self) -> tuple[PeerSession, ...]:
        return await self._service.list_agents()

    async def send_message(self, target: str, message: str) -> PeerSendResult:
        return await self._service.send_message(target, message)

    async def rename(self, name: str) -> PeerSession:
        return await self._service.rename(name)

    async def dispatch(
        self,
        request: ActionRequest,
        cancellation: CancellationToken,
        task_authorization: TaskAuthorization | None = None,
        *,
        execution_context: ActionExecutionContext | None = None,
    ) -> ActionResult:
        del task_authorization, execution_context
        cancellation.raise_if_cancelled()
        try:
            result = await self._execute(request)
        except PeerAmbiguousError as error:
            return ActionResult(
                request.id,
                request.name,
                {"error": "ambiguous_peer_name", "refs": list(error.refs)},
                True,
            )
        except PeerError as error:
            return ActionResult(
                request.id,
                request.name,
                {"error": type(error).__name__},
                True,
            )
        except (TypeError, ValueError):
            return ActionResult(
                request.id,
                request.name,
                {"error": "invalid_peer_tool_arguments"},
                True,
            )
        cancellation.raise_if_cancelled()
        return result

    async def _execute(self, request: ActionRequest) -> ActionResult:
        if request.name == "list_agents":
            _expect_arguments(request, set())
            agents = await self.list_agents()
            return ActionResult(
                request.id,
                request.name,
                {"agents": [_agent_payload(agent) for agent in agents]},
            )
        if request.name == "send_message":
            _expect_arguments(request, {"target", "message"})
            sent = await self.send_message(
                _argument(request, "target"), _argument(request, "message")
            )
            return ActionResult(
                request.id,
                request.name,
                {
                    "message_id": sent.message.id,
                    "status": sent.message.status.value,
                    "deduplicated": sent.deduplicated,
                },
            )
        if request.name == "rename_agent":
            _expect_arguments(request, {"name"})
            renamed = await self.rename(_argument(request, "name"))
            return ActionResult(
                request.id,
                request.name,
                {"name": renamed.name, "ref": renamed.session_ref},
            )
        return ActionResult(
            request.id, request.name, {"error": "unknown_peer_tool"}, True
        )


def _agent_payload(agent: PeerSession) -> dict[str, object]:
    return {
        "name": agent.name,
        "ref": agent.session_ref,
        "status": agent.status.value,
    }


def validate_peer_tool_arguments(
    name: str, arguments: Mapping[str, object]
) -> str | None:
    """Validate model-facing peer calls without echoing peer message text."""
    if name == "list_agents":
        return None if not arguments else "list_agents accepts no arguments"
    if name != "send_message":
        return None
    if set(arguments) != {"target", "message"}:
        return "send_message requires only target and message"
    target = arguments.get("target")
    if (
        not isinstance(target, str)
        or not target.strip()
        or len(target) > _MAX_PEER_TARGET_CHARACTERS
    ):
        return "target must be non-blank text of at most 80 characters"
    message = arguments.get("message")
    if not isinstance(message, str) or not message.strip():
        return "message must be non-blank text"
    try:
        validate_peer_content(message)
    except (TypeError, ValueError):
        return f"message must fit {MAX_PEER_TEXT_BYTES} escaped UTF-8 bytes"
    return None


def _expect_arguments(request: ActionRequest, expected: set[str]) -> None:
    if set(request.arguments) != expected:
        raise ValueError("invalid peer tool arguments")


def _argument(request: ActionRequest, name: str) -> str:
    value = request.arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value
