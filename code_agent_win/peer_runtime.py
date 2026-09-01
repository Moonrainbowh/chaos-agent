from __future__ import annotations

import asyncio
import time
from pathlib import Path

from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.core.events import EventKind
from code_agent.interfaces.terminal_display import DisplayKind
from code_agent.peers.models import (
    PeerInboundPolicy,
    PeerMessageStatus,
    PeerSession,
    PeerSessionStatus,
)
from code_agent.peers.service import PeerMessagingService
from code_agent_win.peer_context import PeerContextBuilder, PeerDeliveryBuffer
from code_agent_win.peer_runtime_support import peer_preview, thread_is_task_owned


_POLL_SECONDS = 0.5
_HEARTBEAT_SECONDS = 5.0
_RENEW_SECONDS = 5.0
_MAX_AUTO_ATTEMPTS = 3
_MAX_PEER_THREAD_TURNS = 4


class PeerRuntime:
    """Lifecycle-bound same-machine peer registry, inbox pump, and UI facade."""

    def __init__(
        self,
        service: PeerMessagingService,
        workspace_root: Path,
        tui_ref: list[object],
        *,
        permission_mode: str,
        activity_lock: asyncio.Lock | None = None,
    ) -> None:
        self.service = service
        self._root = workspace_root.resolve()
        self._tui_ref = tui_ref
        self._permission_mode = permission_mode
        self._policy = service.session.inbound_policy
        self._state_lock = asyncio.Lock()
        self._activity_lock = activity_lock or asyncio.Lock()
        self._buffer = PeerDeliveryBuffer(service)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._registered = False
        self._held_seen: set[str] = set()
        self._auto_attempts = 0
        self._retry_at = 0.0
        self._auto_suspended = False
        self._peer_thread_id: str | None = None
        self._peer_thread_turns = 0
        self.last_error: str | None = None

    def wrap_context(self, inner: object) -> PeerContextBuilder:
        return PeerContextBuilder(inner, self._buffer)

    async def start(self) -> None:
        if self._registered:
            return
        await self.service.register()
        self._registered = True
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def aclose(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
        if self._registered:
            async with self._activity_lock:
                await self.service.close()
                self._registered = False

    async def list_agents(self) -> tuple[PeerSession, ...]:
        return await self.service.list_agents()

    async def rename(self, name: str) -> PeerSession:
        return await self.service.rename(name)

    async def send_message(self, target: str, text: str) -> object:
        return await self.service.send_message(target, text)

    async def set_inbound_policy(self, value: str) -> PeerSession:
        selected = PeerInboundPolicy(value)
        async with self._state_lock:
            previous = self._policy
            self._policy = selected
            try:
                return await self._heartbeat_locked()
            except Exception:
                self._policy = previous
                raise

    async def list_inbox(self) -> tuple[object, ...]:
        return await self.service.list_inbox()

    async def resolve_held(self, message_id: str, *, accept: bool) -> object:
        result = await self.service.resolve_held(message_id, accept=accept)
        self._held_seen.discard(message_id)
        if accept:
            self._resume_auto_delivery()
        return result

    async def _loop(self) -> None:
        next_heartbeat = 0.0
        next_renew = 0.0
        while not self._stop.is_set():
            try:
                now = time.monotonic()
                if now >= next_heartbeat:
                    await self._heartbeat()
                    next_heartbeat = now + _HEARTBEAT_SECONDS
                if self._buffer.has_pending and now >= next_renew:
                    await self._buffer.renew()
                    next_renew = now + _RENEW_SECONDS
                await self._poll_inbox()
                await self._notify_held()
                self.last_error = None
            except Exception as error:
                self.last_error = type(error).__name__
            try:
                await asyncio.wait_for(self._stop.wait(), _POLL_SECONDS)
            except TimeoutError:
                pass

    async def _heartbeat(self) -> PeerSession:
        async with self._state_lock:
            return await self._heartbeat_locked()

    async def _heartbeat_locked(self) -> PeerSession:
        app = self._app()
        status = PeerSessionStatus.IDLE
        run_task = getattr(app, "_run_task", None) if app else None
        if run_task is not None and not run_task.done():
            status = PeerSessionStatus.RUNNING
        if app is not None and (
            getattr(app, "_pending_approval", None) is not None
            or getattr(app, "_pending_interaction", None) is not None
        ):
            status = PeerSessionStatus.WAITING
        permissions = getattr(app, "permissions", None) if app else None
        current = getattr(permissions, "current", None)
        permission = getattr(current, "name", self._permission_mode)
        return await self.service.heartbeat(
            workspace_root=str(self._root),
            thread_id=getattr(app, "current_thread_id", None) if app else None,
            task_id=getattr(app, "active_task_id", None) if app else None,
            permission_mode=permission,
            status=status,
            inbound_policy=self._policy,
        )

    async def _poll_inbox(self) -> None:
        claims = await self.service.claim_inbox()
        if claims:
            peers = await self.service.list_agents(include_self=True)
            senders = {
                item.instance_id: (item.name, item.session_ref) for item in peers
            }
            app = self._app()
            for claim in claims:
                name, ref = senders.get(
                    claim.message.sender_instance_id,
                    (claim.message.sender_instance_id[:12], "unknown"),
                )
                is_new = await self._buffer.offer(claim, name, ref)
                if is_new:
                    self._resume_auto_delivery()
                    if app is not None:
                        self._display(
                            app,
                            f"peer · {name} [{ref}]: {peer_preview(claim.message.content)}",
                        )
        await self._maybe_wake()

    async def _notify_held(self) -> None:
        held = await self.service.list_inbox((PeerMessageStatus.HELD,))
        current = {item.id for item in held}
        app = self._app()
        if app is not None:
            for message in held:
                if message.id not in self._held_seen:
                    self._display(
                        app,
                        f"peer · held {message.id[:12]}; use /会话 待处理",
                    )
        self._held_seen.intersection_update(current)
        self._held_seen.update(current)

    async def _maybe_wake(self) -> None:
        app = self._app()
        if not self._can_auto_wake(app):
            return
        async with self._activity_lock:
            if not self._can_auto_wake(app) or await thread_is_task_owned(app):
                return
            self._wake_idle_tui(app)

    def _can_auto_wake(self, app: object | None) -> bool:
        run_task = getattr(app, "_run_task", None) if app else None
        return bool(
            self._buffer.has_pending
            and app is not None
            and getattr(app, "running", False)
            and not getattr(app, "_closing", False)
            and getattr(app, "active_task_id", None) is None
            and (run_task is None or run_task.done())
            and not self._auto_suspended
            and time.monotonic() >= self._retry_at
        )

    def _wake_idle_tui(self, app: object) -> None:
        token = CancellationToken()
        app._token = token
        app._run_started_at = time.monotonic()
        app.state.begin_run()
        task = asyncio.create_task(self._consume_peer(app, token))
        app._peer_run_task = task
        app._run_task = task
        app._start_animation()
        app.redraw()

    async def _consume_peer(
        self, app: object, token: CancellationToken
    ) -> None:
        before = self._buffer.pending_count
        try:
            async for event in app.controller.receive_peer(
                thread_id=self._peer_thread_id, cancellation=token
            ):
                app.state.apply(event)
                if (
                    event.kind is EventKind.RUN_STARTED
                    and self._peer_thread_id is None
                    and app.state.thread_id
                ):
                    self._peer_thread_id = app.state.thread_id
                if event.kind is not EventKind.MODEL_EVENT:
                    app._flush_pending_entries()
                app._request_redraw(
                    immediate=event.kind is not EventKind.MODEL_EVENT
                )
        except CancellationError:
            app.state.status = "paused"
        except Exception as error:
            self._record_auto_failure()
            self._display(app, f"peer delivery: {type(error).__name__}", error=True)
        else:
            if self._buffer.pending_count < before:
                self._peer_thread_turns += 1
                if self._peer_thread_turns >= _MAX_PEER_THREAD_TURNS:
                    self._peer_thread_id = None
                    self._peer_thread_turns = 0
                self._resume_auto_delivery()
            elif self._buffer.has_pending:
                self._auto_suspended = True
                self._display(app, "peer context deferred to the next user turn")
        finally:
            if getattr(app, "_peer_run_task", None) is asyncio.current_task():
                app._peer_run_task = None

    def _record_auto_failure(self) -> None:
        self._auto_attempts += 1
        if self._auto_attempts >= _MAX_AUTO_ATTEMPTS:
            self._auto_suspended = True
            return
        self._retry_at = time.monotonic() + 2 ** self._auto_attempts

    def _resume_auto_delivery(self) -> None:
        self._auto_attempts = 0
        self._retry_at = 0.0
        self._auto_suspended = False

    def _display(self, app: object, text: str, *, error: bool = False) -> None:
        app._append(DisplayKind.ERROR if error else DisplayKind.METADATA, text)
        app._request_redraw(immediate=True)

    def _app(self) -> object | None:
        return self._tui_ref[0] if self._tui_ref else None
# discovery: steering persists role=user, so it is never reused for PEER input.
# human_decision: peer text is plain text only and has no user permission authority.
# deviation: Claude's native feature excludes Windows, so this runtime uses local SQLite.
