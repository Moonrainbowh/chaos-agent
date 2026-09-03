from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import acp.schema as schema
from acp import PROTOCOL_VERSION, RequestError

from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import Message
from code_agent.policy.models import ApprovalMode
from code_agent.sessions.errors import SessionNotFound

from .content import history_update, prompt_text
from .event_bridge import acp_stop_reason, acp_updates
from .session_permissions import SessionPermissions


class ChaosAcpAgent:
    """Expose one configured Chaos Agent application over ACP v1."""

    def __init__(
        self,
        controller: object,
        sessions: object,
        workspace_root: Path,
        *,
        version: str = "unknown",
        permission_scope: Callable[
            [ApprovalMode, str], AbstractContextManager[None]
        ]
        | None = None,
    ) -> None:
        if not hasattr(controller, "ask"):
            raise TypeError("controller must provide ask")
        if not hasattr(sessions, "create_thread") or not hasattr(
            sessions, "load_messages"
        ):
            raise TypeError("sessions must provide create_thread and load_messages")
        if not isinstance(workspace_root, Path):
            raise TypeError("workspace_root must be a Path")
        self._controller = controller
        self._sessions = sessions
        self._root = workspace_root.resolve()
        self._version = version
        self._permissions = SessionPermissions(permission_scope)
        self._client: object | None = None
        self._prompt_lock = asyncio.Lock()
        self._active: dict[str, CancellationToken] = {}

    def on_connect(self, conn: object) -> None:
        if not hasattr(conn, "session_update"):
            raise TypeError("ACP client must provide session_update")
        self._client = conn
        self._permissions.reset()

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: object | None = None,
        client_info: object | None = None,
        **kwargs: Any,
    ) -> schema.InitializeResponse:
        del client_capabilities, client_info, kwargs
        if protocol_version != PROTOCOL_VERSION:
            raise RequestError.invalid_params(
                {"protocolVersion": f"ACP v{PROTOCOL_VERSION} is required"}
            )
        return schema.InitializeResponse(
            protocolVersion=PROTOCOL_VERSION,
            agentCapabilities=schema.AgentCapabilities(
                loadSession=True,
                promptCapabilities=schema.PromptCapabilities(
                    image=False,
                    audio=False,
                    embeddedContext=False,
                ),
                sessionCapabilities=schema.SessionCapabilities(
                    list=schema.SessionListCapabilities()
                ),
            ),
            agentInfo=schema.Implementation(
                name="chaos-agent", title="Chaos Agent", version=self._version
            ),
        )

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[object] | None = None,
        **kwargs: Any,
    ) -> schema.NewSessionResponse:
        del kwargs
        self._validate_setup(cwd, additional_directories, mcp_servers)
        thread_id = await self._sessions.create_thread()
        self._permissions.open(thread_id)
        return schema.NewSessionResponse(
            sessionId=thread_id, modes=self._permissions.state(thread_id)
        )

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[object] | None = None,
        additional_directories: list[str] | None = None,
        **kwargs: Any,
    ) -> schema.LoadSessionResponse:
        del kwargs
        self._validate_setup(cwd, additional_directories, mcp_servers)
        messages = await self._messages(session_id)
        self._permissions.ensure(session_id)
        client = self._require_client()
        for message in messages:
            update = history_update(message)
            if update is not None:
                await client.session_update(session_id, update)
        return schema.LoadSessionResponse(modes=self._permissions.state(session_id))

    async def list_sessions(
        self,
        cwd: str | None = None,
        cursor: str | None = None,
        **kwargs: Any,
    ) -> schema.ListSessionsResponse:
        del kwargs
        if cwd is not None:
            self._validate_cwd(cwd)
        if cursor is not None:
            raise RequestError.invalid_params({"cursor": "pagination is not supported"})
        summaries = await self._sessions.list_threads(limit=1_000)
        sessions = [
            schema.SessionInfo(
                sessionId=item.id,
                cwd=str(self._root),
                title=item.title,
                updatedAt=item.updated_at.isoformat(),
            )
            for item in summaries
        ]
        return schema.ListSessionsResponse(sessions=sessions)

    async def set_session_mode(
        self, session_id: str, mode_id: str, **kwargs: Any
    ) -> schema.SetSessionModeResponse:
        del kwargs
        await self._messages(session_id)
        if session_id in self._active:
            raise RequestError.invalid_request(
                {"sessionId": "permission mode cannot change during an active prompt"}
            )
        self._permissions.set(session_id, mode_id)
        return schema.SetSessionModeResponse()

    async def close_session(
        self, session_id: str, **kwargs: Any
    ) -> schema.CloseSessionResponse:
        del kwargs
        await self._messages(session_id)
        token = self._active.get(session_id)
        if token is not None:
            token.cancel("ACP session closed")
        self._permissions.close(session_id)
        return schema.CloseSessionResponse()

    async def prompt(
        self,
        session_id: str,
        prompt: list[object],
        **kwargs: Any,
    ) -> schema.PromptResponse:
        del kwargs
        await self._messages(session_id)
        text = prompt_text(prompt)
        if session_id in self._active:
            raise RequestError.invalid_request(
                {"sessionId": "a prompt is already active"}
            )
        token = CancellationToken()
        self._active[session_id] = token
        stop_reason: str | None = None
        try:
            async with self._prompt_lock:
                with self._permissions.scope(session_id):
                    async for event in self._controller.ask(
                        text, thread_id=session_id, cancellation=token
                    ):
                        for update in acp_updates(event):
                            await self._require_client().session_update(session_id, update)
                        stop_reason = acp_stop_reason(event) or stop_reason
        except Exception as error:
            if stop_reason != "refusal":
                raise RequestError.internal_error(
                    {"errorType": type(error).__name__}
                ) from None
        finally:
            self._active.pop(session_id, None)
        if stop_reason is None:
            raise RequestError.internal_error(
                {"errorType": "MissingTerminalAgentEvent"}
            )
        return schema.PromptResponse(stopReason=stop_reason)

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        del kwargs
        token = self._active.get(session_id)
        if token is not None:
            token.cancel("cancelled by ACP client")

    async def ext_method(
        self, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        del params
        raise RequestError.method_not_found(method)

    async def ext_notification(
        self, method: str, params: dict[str, Any]
    ) -> None:
        del method, params

    def _validate_setup(
        self,
        cwd: str,
        additional_directories: Sequence[str] | None,
        mcp_servers: Sequence[object] | None,
    ) -> None:
        self._validate_cwd(cwd)
        if additional_directories:
            raise RequestError.invalid_params(
                {"additionalDirectories": "not supported"}
            )
        if mcp_servers:
            raise RequestError.invalid_params({"mcpServers": "not supported"})

    def _validate_cwd(self, cwd: str) -> None:
        if not isinstance(cwd, str) or not cwd.strip():
            raise RequestError.invalid_params({"cwd": "must be an absolute path"})
        candidate = Path(cwd)
        if not candidate.is_absolute() or candidate.resolve() != self._root:
            raise RequestError.invalid_params(
                {"cwd": "must match the configured Chaos Agent workspace"}
            )

    async def _messages(self, session_id: str) -> tuple[Message, ...]:
        if not isinstance(session_id, str) or not session_id.strip():
            raise RequestError.invalid_params({"sessionId": "must be non-blank"})
        try:
            messages = tuple(await self._sessions.load_messages(session_id))
        except SessionNotFound:
            raise RequestError.resource_not_found(session_id) from None
        if not all(isinstance(message, Message) for message in messages):
            raise RequestError.internal_error({"errorType": "InvalidSessionHistory"})
        return messages

    def _require_client(self) -> object:
        if self._client is None:
            raise RequestError.internal_error({"errorType": "ClientNotConnected"})
        return self._client

__all__ = ["ChaosAcpAgent"]
