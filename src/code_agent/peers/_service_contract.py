from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .models import (
    MAX_PEER_TEXT_BYTES,
    PeerClaim,
    PeerMessage,
    PeerMessageStatus,
    PeerSendResult,
    PeerSession,
)


@dataclass(frozen=True)
class PeerServiceLimits:
    heartbeat_ttl_seconds: int = 30
    message_ttl_seconds: int = 86_400
    held_ttl_seconds: int = 300
    claim_ttl_seconds: int = 30
    dedupe_window_seconds: int = 30
    rate_window_seconds: int = 60
    rate_limit: int = 30
    queued_limit: int = 50
    held_limit: int = 100
    max_claim_batch: int = 20
    max_text_bytes: int = MAX_PEER_TEXT_BYTES

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field_name} must be an integer")
            if value <= 0:
                raise ValueError(f"{field_name} must be positive")
        if self.max_claim_batch > 100:
            raise ValueError("max_claim_batch must not exceed 100")
        if self.max_text_bytes > MAX_PEER_TEXT_BYTES:
            raise ValueError("max_text_bytes exceeds the peer message hard limit")


class PeerStore(Protocol):
    async def register_peer_session(self, session: PeerSession) -> PeerSession: ...

    async def rename_peer_session(
        self, instance_id: str, owner_pid: int, owner_create_time: float,
        name: str, now: datetime,
    ) -> PeerSession: ...

    async def close_peer_session(
        self, instance_id: str, owner_pid: int, owner_create_time: float,
        now: datetime,
    ) -> PeerSession: ...

    async def list_peer_sessions(
        self, stale_before: datetime, *, exclude_instance_id: str | None,
    ) -> tuple[PeerSession, ...]: ...

    async def enqueue_peer_message(
        self, *, sender_instance_id: str, owner_pid: int,
        owner_create_time: float, target: str, content: str, now: datetime,
        stale_before: datetime, queued_expires_at: datetime,
        held_expires_at: datetime, dedupe_after: datetime,
        rate_after: datetime, rate_limit: int, queued_limit: int,
        held_limit: int,
    ) -> PeerSendResult: ...

    async def list_peer_inbox(
        self, receiver_instance_id: str, owner_pid: int,
        owner_create_time: float, statuses: tuple[PeerMessageStatus, ...],
        now: datetime, limit: int,
    ) -> tuple[PeerMessage, ...]: ...

    async def claim_peer_messages(
        self, receiver_instance_id: str, owner_pid: int,
        owner_create_time: float, now: datetime,
        claim_expires_at: datetime, limit: int,
    ) -> tuple[PeerClaim, ...]: ...

    async def renew_peer_message_claim(
        self, receiver_instance_id: str, owner_pid: int,
        owner_create_time: float, message_id: str, claim_token: str,
        now: datetime, claim_expires_at: datetime,
    ) -> PeerClaim: ...

    async def get_peer_message(
        self, receiver_instance_id: str, owner_pid: int,
        owner_create_time: float, message_id: str, now: datetime,
    ) -> PeerMessage: ...

    async def resolve_held_peer_message(
        self, receiver_instance_id: str, owner_pid: int,
        owner_create_time: float, message_id: str, accept: bool,
        now: datetime, accepted_expires_at: datetime, queued_limit: int,
    ) -> PeerMessage: ...

    async def acknowledge_peer_message(
        self, receiver_instance_id: str, owner_pid: int,
        owner_create_time: float, message_id: str, claim_token: str,
        outcome: PeerMessageStatus, now: datetime,
    ) -> PeerMessage: ...


def looks_like_store(value: object) -> bool:
    required = (
        "register_peer_session", "rename_peer_session", "close_peer_session",
        "list_peer_sessions", "enqueue_peer_message", "list_peer_inbox",
        "claim_peer_messages", "renew_peer_message_claim", "get_peer_message",
        "resolve_held_peer_message",
        "acknowledge_peer_message",
    )
    return all(callable(getattr(value, name, None)) for name in required)
