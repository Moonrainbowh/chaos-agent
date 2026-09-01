from __future__ import annotations

import math
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


MAX_PEER_TEXT_BYTES = 4_096


class PeerOrigin(str, Enum):
    PEER = "peer"


class PeerSessionStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    CLOSED = "closed"


class PeerInboundPolicy(str, Enum):
    AUTO = "auto"
    ACCEPT = "accept"
    HOLD = "hold"
    REFUSE = "refuse"


class PeerMessageStatus(str, Enum):
    QUEUED = "queued"
    HELD = "held"
    DELIVERED = "delivered"
    REFUSED = "refused"
    EXPIRED = "expired"


def _text(value: object, name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
    if len(value) > limit:
        raise ValueError(f"{name} must be at most {limit} characters")
    return value


def _optional_text(value: object, name: str, limit: int) -> str | None:
    if value is None:
        return None
    return _text(value, name, limit)


def validate_peer_content(value: object) -> str:
    """Return text whose escaped prompt representation fits the hard limit."""
    content = _text(value, "content", MAX_PEER_TEXT_BYTES)
    try:
        raw_bytes = len(content.encode("utf-8"))
        rendered = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
        rendered = (
            rendered.replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
        )
        rendered_bytes = len(rendered.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("content must be valid UTF-8 text") from None
    if raw_bytes > MAX_PEER_TEXT_BYTES or rendered_bytes > MAX_PEER_TEXT_BYTES:
        raise ValueError(
            f"content must fit {MAX_PEER_TEXT_BYTES} escaped UTF-8 bytes"
        )
    return content


def _utc(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class PeerSession:
    instance_id: str
    session_ref: str
    name: str
    owner_pid: int
    owner_create_time: float
    workspace_root: str
    thread_id: str | None
    task_id: str | None
    permission_mode: str
    inbound_policy: PeerInboundPolicy
    status: PeerSessionStatus
    heartbeat_at: datetime
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for field_name, limit in (
            ("instance_id", 128), ("session_ref", 64), ("name", 80),
            ("workspace_root", 4_096), ("permission_mode", 64),
        ):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name, limit)
            )
        for field_name in ("thread_id", "task_id"):
            object.__setattr__(
                self,
                field_name,
                _optional_text(getattr(self, field_name), field_name, 128),
            )
        if isinstance(self.owner_pid, bool) or not isinstance(self.owner_pid, int):
            raise TypeError("owner_pid must be an integer")
        if self.owner_pid <= 0:
            raise ValueError("owner_pid must be positive")
        if isinstance(self.owner_create_time, bool) or not isinstance(
            self.owner_create_time, (int, float)
        ):
            raise TypeError("owner_create_time must be numeric")
        created_identity = float(self.owner_create_time)
        if not math.isfinite(created_identity) or created_identity <= 0:
            raise ValueError("owner_create_time must be positive and finite")
        object.__setattr__(self, "owner_create_time", created_identity)
        if not isinstance(self.inbound_policy, PeerInboundPolicy):
            raise TypeError("inbound_policy must be a PeerInboundPolicy")
        if not isinstance(self.status, PeerSessionStatus):
            raise TypeError("status must be a PeerSessionStatus")
        created = _utc(self.created_at, "created_at")
        heartbeat = _utc(self.heartbeat_at, "heartbeat_at")
        updated = _utc(self.updated_at, "updated_at")
        if heartbeat < created or updated < heartbeat:
            raise ValueError("peer timestamps must not precede created_at")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "heartbeat_at", heartbeat)
        object.__setattr__(self, "updated_at", updated)


@dataclass(frozen=True)
class PeerMessage:
    id: str
    sender_instance_id: str
    receiver_instance_id: str
    content: str
    status: PeerMessageStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    origin: PeerOrigin = PeerOrigin.PEER
    claim_token: str | None = None
    claimed_at: datetime | None = None
    claim_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in ("id", "sender_instance_id", "receiver_instance_id"):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name, 128)
            )
        content = validate_peer_content(self.content)
        object.__setattr__(self, "content", content)
        if self.origin is not PeerOrigin.PEER:
            raise ValueError("peer messages must have PEER origin")
        if not isinstance(self.status, PeerMessageStatus):
            raise TypeError("status must be a PeerMessageStatus")
        created = _utc(self.created_at, "created_at")
        updated = _utc(self.updated_at, "updated_at")
        expires = _utc(self.expires_at, "expires_at")
        if updated < created or expires <= created:
            raise ValueError("peer message timestamps are inconsistent")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)
        object.__setattr__(self, "expires_at", expires)
        token = _optional_text(self.claim_token, "claim_token", 128)
        object.__setattr__(self, "claim_token", token)
        claimed = None if self.claimed_at is None else _utc(self.claimed_at, "claimed_at")
        claim_expires = (
            None
            if self.claim_expires_at is None
            else _utc(self.claim_expires_at, "claim_expires_at")
        )
        if (token is None) != (claimed is None) or (token is None) != (
            claim_expires is None
        ):
            raise ValueError("claim fields must be present or absent together")
        if token is not None:
            if self.status is not PeerMessageStatus.QUEUED:
                raise ValueError("only queued messages may be claimed")
            if claimed < created or updated < claimed:  # type: ignore[operator]
                raise ValueError("claim timestamps must follow message creation")
            if claim_expires <= claimed:  # type: ignore[operator]
                raise ValueError("claim_expires_at must follow claimed_at")
        object.__setattr__(self, "claimed_at", claimed)
        object.__setattr__(self, "claim_expires_at", claim_expires)


@dataclass(frozen=True)
class PeerClaim:
    message: PeerMessage
    token: str

    def __post_init__(self) -> None:
        if not isinstance(self.message, PeerMessage):
            raise TypeError("message must be a PeerMessage")
        token = _text(self.token, "token", 128)
        if self.message.claim_token != token:
            raise ValueError("claim token must match the message")
        object.__setattr__(self, "token", token)


@dataclass(frozen=True)
class PeerSendResult:
    message: PeerMessage
    deduplicated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.message, PeerMessage):
            raise TypeError("message must be a PeerMessage")
        if not isinstance(self.deduplicated, bool):
            raise TypeError("deduplicated must be a bool")
