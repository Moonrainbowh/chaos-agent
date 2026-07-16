from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum

from code_agent.core.models import Message, Usage


_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _text(value: object, label: str, maximum: int = 16_384) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must be non-blank bounded text")
    return value


def _nonnegative(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


class SourceKind(str, Enum):
    MESSAGE = "message"
    EVENT = "event"
    CHECKPOINT = "checkpoint"
    EVIDENCE = "evidence"


class EntryRelation(str, Enum):
    NONE = "none"
    SUPERSEDES = "supersedes"
    REVERTS = "reverts"


@dataclass(frozen=True)
class SourceAnchor:
    thread_id: str
    kind: SourceKind
    sequence: int
    stable_id: str
    digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "thread_id", _text(self.thread_id, "thread_id", 256))
        if not isinstance(self.kind, SourceKind):
            raise TypeError("kind must be SourceKind")
        object.__setattr__(self, "sequence", _nonnegative(self.sequence, "sequence"))
        object.__setattr__(self, "stable_id", _text(self.stable_id, "stable_id", 512))
        if not isinstance(self.digest, str) or not _DIGEST.fullmatch(self.digest):
            raise ValueError("digest must be a SHA-256 hex digest")


@dataclass(frozen=True)
class AnchoredMessage:
    anchor: SourceAnchor
    message: Message

    def __post_init__(self) -> None:
        if self.anchor.kind is not SourceKind.MESSAGE:
            raise ValueError("message anchor must have message kind")
        if not isinstance(self.message, Message):
            raise TypeError("message must be Message")
        if self.anchor.digest != message_digest(self.message):
            raise ValueError("message digest does not match anchor")


@dataclass(frozen=True)
class SummaryRequest:
    sources: tuple[AnchoredMessage, ...]
    max_output_tokens: int
    max_total_tokens: int

    def __post_init__(self) -> None:
        sources = tuple(self.sources)
        if not sources or any(not isinstance(item, AnchoredMessage) for item in sources):
            raise ValueError("sources must contain anchored messages")
        threads = {item.anchor.thread_id for item in sources}
        if len(threads) != 1:
            raise ValueError("summary sources must belong to one thread")
        object.__setattr__(self, "sources", sources)
        if _nonnegative(self.max_output_tokens, "max_output_tokens") == 0:
            raise ValueError("max_output_tokens must be positive")
        if _nonnegative(self.max_total_tokens, "max_total_tokens") == 0:
            raise ValueError("max_total_tokens must be positive")
        if self.max_total_tokens < self.max_output_tokens:
            raise ValueError("max_total_tokens must cover max_output_tokens")


@dataclass(frozen=True)
class SummaryResponse:
    summary: str
    model: str
    usage: Usage
    version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary", _text(self.summary, "summary"))
        object.__setattr__(self, "model", _text(self.model, "model", 512))
        if not isinstance(self.usage, Usage):
            raise TypeError("usage must be Usage")
        if _nonnegative(self.version, "version") == 0:
            raise ValueError("version must be positive")


@dataclass(frozen=True)
class SemanticCheckpoint:
    id: str
    thread_id: str
    source_start: SourceAnchor
    source_end: SourceAnchor
    source_digest: str
    summary: str
    model: str
    usage: Usage
    version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "id", 128))
        object.__setattr__(self, "thread_id", _text(self.thread_id, "thread_id", 256))
        if self.source_start.thread_id != self.thread_id or self.source_end.thread_id != self.thread_id:
            raise ValueError("checkpoint source thread mismatch")
        if self.source_end.sequence < self.source_start.sequence:
            raise ValueError("checkpoint source range is reversed")
        if not _DIGEST.fullmatch(self.source_digest):
            raise ValueError("source_digest must be a SHA-256 hex digest")
        object.__setattr__(self, "summary", _text(self.summary, "summary"))
        object.__setattr__(self, "model", _text(self.model, "model", 512))
        if not isinstance(self.usage, Usage):
            raise TypeError("usage must be Usage")
        if _nonnegative(self.version, "version") == 0:
            raise ValueError("version must be positive")

    @classmethod
    def create(cls, sources: tuple[AnchoredMessage, ...], response: SummaryResponse) -> "SemanticCheckpoint":
        digest = source_range_digest(sources)
        identity = hashlib.sha256(
            json.dumps(
                {
                    "thread_id": sources[0].anchor.thread_id,
                    "start": sources[0].anchor.stable_id,
                    "end": sources[-1].anchor.stable_id,
                    "source_digest": digest,
                    "model": response.model,
                    "version": response.version,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return cls(
            f"semantic-{identity[:32]}",
            sources[0].anchor.thread_id,
            sources[0].anchor,
            sources[-1].anchor,
            digest,
            response.summary,
            response.model,
            response.usage,
            response.version,
        )


@dataclass(frozen=True)
class ThreadEntry:
    anchor: SourceAnchor
    text: str
    relation: EntryRelation = EntryRelation.NONE
    related_id: str | None = None
    tool_call_id: str | None = None
    tool_is_error: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _text(self.text, "text"))
        if not isinstance(self.relation, EntryRelation):
            raise TypeError("relation must be EntryRelation")
        if self.relation is EntryRelation.NONE and self.related_id is not None:
            raise ValueError("unrelated entry cannot have related_id")
        if self.relation is not EntryRelation.NONE:
            object.__setattr__(self, "related_id", _text(self.related_id, "related_id", 512))
        if self.tool_call_id is not None:
            object.__setattr__(self, "tool_call_id", _text(self.tool_call_id, "tool_call_id", 512))
        if self.tool_is_error is not None and not isinstance(self.tool_is_error, bool):
            raise TypeError("tool_is_error must be bool or None")


@dataclass(frozen=True)
class SearchHit:
    entry: ThreadEntry
    score: int


@dataclass(frozen=True)
class ThreadRead:
    primary: ThreadEntry
    later_revisions: tuple[ThreadEntry, ...] = field(default_factory=tuple)
    tool_outcomes: tuple[ThreadEntry, ...] = field(default_factory=tuple)
    conflict_candidate: bool = False


def anchor_message(thread_id: str, sequence: int, message: Message) -> AnchoredMessage:
    digest = message_digest(message)
    anchor = SourceAnchor(thread_id, SourceKind.MESSAGE, sequence, f"{thread_id}:message:{sequence}", digest)
    return AnchoredMessage(anchor, message)


def message_digest(message: Message) -> str:
    payload = json.dumps(message.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def source_range_digest(sources: tuple[AnchoredMessage, ...]) -> str:
    payload = "\n".join(item.anchor.digest for item in sources)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()
