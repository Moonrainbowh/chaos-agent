from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .errors import PeerIdentityError
from .models import (
    PeerClaim,
    PeerInboundPolicy,
    PeerMessage,
    PeerMessageStatus,
    PeerSendResult,
    PeerSession,
    PeerSessionStatus,
    validate_peer_content,
)
from ._service_contract import PeerServiceLimits, PeerStore, looks_like_store


class PeerMessagingService:
    """Bind peer operations to one registered local process identity."""

    def __init__(
        self,
        store: PeerStore,
        *,
        owner_pid: int,
        owner_create_time: float,
        workspace_root: str,
        permission_mode: str,
        name: str,
        thread_id: str | None = None,
        task_id: str | None = None,
        inbound_policy: PeerInboundPolicy = PeerInboundPolicy.AUTO,
        status: PeerSessionStatus = PeerSessionStatus.IDLE,
        instance_id: str | None = None,
        session_ref: str | None = None,
        limits: PeerServiceLimits | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not looks_like_store(store):
            raise TypeError("store does not implement PeerStore")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        self._store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._limits = limits or PeerServiceLimits()
        now = self._now()
        self._session = PeerSession(
            instance_id=instance_id or uuid.uuid4().hex,
            session_ref=session_ref or uuid.uuid4().hex[:12],
            name=name,
            owner_pid=owner_pid,
            owner_create_time=owner_create_time,
            workspace_root=workspace_root,
            thread_id=thread_id,
            task_id=task_id,
            permission_mode=permission_mode,
            inbound_policy=inbound_policy,
            status=status,
            heartbeat_at=now,
            created_at=now,
            updated_at=now,
        )
        self._registered = False
        self._closed = False
        self._operation_lock = asyncio.Lock()

    @property
    def session(self) -> PeerSession:
        return self._session

    async def register(self) -> PeerSession:
        async with self._operation_lock:
            if self._registered:
                return self._session
            if self._closed:
                raise PeerIdentityError("closed peer service cannot register again")
            now = self._now()
            candidate = replace(self._session, heartbeat_at=now, updated_at=now)
            self._session = await self._store.register_peer_session(candidate)
            self._registered = True
            return self._session

    async def heartbeat(
        self,
        *,
        workspace_root: str,
        thread_id: str | None,
        task_id: str | None,
        permission_mode: str,
        status: PeerSessionStatus,
        inbound_policy: PeerInboundPolicy | None = None,
    ) -> PeerSession:
        async with self._operation_lock:
            self._require_registered()
            now = self._now()
            candidate = replace(
                self._session,
                workspace_root=workspace_root,
                thread_id=thread_id,
                task_id=task_id,
                permission_mode=permission_mode,
                inbound_policy=inbound_policy or self._session.inbound_policy,
                status=status,
                heartbeat_at=now,
                updated_at=now,
            )
            self._session = await self._store.register_peer_session(candidate)
            return self._session

    async def rename(self, name: str) -> PeerSession:
        async with self._operation_lock:
            self._require_registered()
            self._session = await self._store.rename_peer_session(
                self._session.instance_id,
                self._session.owner_pid,
                self._session.owner_create_time,
                name,
                self._now(),
            )
            return self._session

    async def close(self) -> PeerSession:
        async with self._operation_lock:
            self._require_registered()
            self._session = await self._store.close_peer_session(
                self._session.instance_id,
                self._session.owner_pid,
                self._session.owner_create_time,
                self._now(),
            )
            self._registered = False
            self._closed = True
            return self._session

    async def list_agents(self, *, include_self: bool = False) -> tuple[PeerSession, ...]:
        async with self._operation_lock:
            self._require_registered()
            now = self._now()
            return await self._store.list_peer_sessions(
                now - timedelta(seconds=self._limits.heartbeat_ttl_seconds),
                exclude_instance_id=None if include_self else self._session.instance_id,
            )

    async def send_message(self, target: str, content: str) -> PeerSendResult:
        async with self._operation_lock:
            self._require_registered()
            content = validate_peer_content(content)
            if len(content.encode("utf-8")) > self._limits.max_text_bytes:
                raise ValueError("peer message exceeds configured text limit")
            now = self._now()
            return await self._store.enqueue_peer_message(
                sender_instance_id=self._session.instance_id,
                owner_pid=self._session.owner_pid,
                owner_create_time=self._session.owner_create_time,
                target=target,
                content=content,
                now=now,
                stale_before=now - timedelta(seconds=self._limits.heartbeat_ttl_seconds),
                queued_expires_at=now + timedelta(seconds=self._limits.message_ttl_seconds),
                held_expires_at=now + timedelta(seconds=self._limits.held_ttl_seconds),
                dedupe_after=now - timedelta(seconds=self._limits.dedupe_window_seconds),
                rate_after=now - timedelta(seconds=self._limits.rate_window_seconds),
                rate_limit=self._limits.rate_limit,
                queued_limit=self._limits.queued_limit,
                held_limit=self._limits.held_limit,
            )

    async def list_inbox(
        self,
        statuses: tuple[PeerMessageStatus, ...] = (
            PeerMessageStatus.QUEUED,
            PeerMessageStatus.HELD,
        ),
        *,
        limit: int = 100,
    ) -> tuple[PeerMessage, ...]:
        async with self._operation_lock:
            self._require_known_identity()
            return await self._store.list_peer_inbox(
                self._session.instance_id,
                self._session.owner_pid,
                self._session.owner_create_time,
                statuses,
                self._now(),
                limit,
            )

    async def claim_inbox(self, *, limit: int | None = None) -> tuple[PeerClaim, ...]:
        async with self._operation_lock:
            self._require_registered()
            batch = self._limits.max_claim_batch if limit is None else limit
            if isinstance(batch, bool) or not isinstance(batch, int):
                raise TypeError("limit must be an integer")
            if batch <= 0 or batch > self._limits.max_claim_batch:
                raise ValueError("limit exceeds the configured claim batch")
            now = self._now()
            return await self._store.claim_peer_messages(
                self._session.instance_id,
                self._session.owner_pid,
                self._session.owner_create_time,
                now,
                now + timedelta(seconds=self._limits.claim_ttl_seconds),
                batch,
            )

    async def renew_claim(self, claim: PeerClaim) -> PeerClaim:
        if not isinstance(claim, PeerClaim):
            raise TypeError("claim must be a PeerClaim")
        async with self._operation_lock:
            self._require_registered()
            now = self._now()
            return await self._store.renew_peer_message_claim(
                self._session.instance_id,
                self._session.owner_pid,
                self._session.owner_create_time,
                claim.message.id,
                claim.token,
                now,
                now + timedelta(seconds=self._limits.claim_ttl_seconds),
            )

    async def get_message(self, message_id: str) -> PeerMessage:
        async with self._operation_lock:
            self._require_known_identity()
            return await self._store.get_peer_message(
                self._session.instance_id,
                self._session.owner_pid,
                self._session.owner_create_time,
                message_id,
                self._now(),
            )

    async def resolve_held(self, message_id: str, *, accept: bool) -> PeerMessage:
        async with self._operation_lock:
            self._require_registered()
            now = self._now()
            return await self._store.resolve_held_peer_message(
                self._session.instance_id,
                self._session.owner_pid,
                self._session.owner_create_time,
                message_id,
                accept,
                now,
                now + timedelta(seconds=self._limits.message_ttl_seconds),
                self._limits.queued_limit,
            )

    async def acknowledge(
        self,
        message_id: str,
        claim_token: str,
        *,
        outcome: PeerMessageStatus = PeerMessageStatus.DELIVERED,
    ) -> PeerMessage:
        async with self._operation_lock:
            self._require_registered()
            if outcome not in {PeerMessageStatus.DELIVERED, PeerMessageStatus.REFUSED}:
                raise ValueError("acknowledgement outcome must be delivered or refused")
            return await self._store.acknowledge_peer_message(
                self._session.instance_id,
                self._session.owner_pid,
                self._session.owner_create_time,
                message_id,
                claim_token,
                outcome,
                self._now(),
            )

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime):
            raise TypeError("clock must return datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    def _require_registered(self) -> None:
        if not self._registered:
            raise PeerIdentityError("peer service is not registered")

    def _require_known_identity(self) -> None:
        if not self._registered and not self._closed:
            raise PeerIdentityError("peer service is not registered")
