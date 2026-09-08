from __future__ import annotations

import asyncio
import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from code_agent.core.attachments import (
    MAX_MESSAGE_ATTACHMENTS,
    MAX_MESSAGE_ATTACHMENT_BYTES,
    AttachmentRef,
    freeze_attachments,
)


_SUPPORTED_SUFFIXES = frozenset(
    {
        ".bmp",
        ".c",
        ".cpp",
        ".cs",
        ".css",
        ".csv",
        ".gif",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".ini",
        ".java",
        ".jpeg",
        ".jpg",
        ".js",
        ".json",
        ".jsx",
        ".log",
        ".md",
        ".png",
        ".ps1",
        ".py",
        ".pyi",
        ".rs",
        ".rst",
        ".scss",
        ".sh",
        ".sql",
        ".toml",
        ".ts",
        ".tsv",
        ".tsx",
        ".txt",
        ".webp",
        ".xml",
        ".yaml",
        ".yml",
    }
)
DEFAULT_ATTACHMENT_PROMPT = "Analyze the attached input."


@dataclass(frozen=True)
class PreparedInput:
    prompt: str
    display: str
    attachments: tuple[AttachmentRef, ...]
    from_draft: bool


class AttachmentDraft:
    """Hold safe attachment references while ingestion remains injectable."""

    def __init__(
        self,
        ingestor: object,
        *,
        validate: Callable[[tuple[AttachmentRef, ...]], None] | None = None,
    ) -> None:
        if not all(hasattr(ingestor, name) for name in ("ingest_paths", "ingest_clipboard")):
            raise TypeError("attachment ingestor is incomplete")
        if validate is not None and not callable(validate):
            raise TypeError("attachment validator must be callable")
        self._ingestor = ingestor
        self._validate = validate
        self._items: tuple[AttachmentRef, ...] = ()
        self._image_numbers: dict[str, int] = {}
        self._next_image_number = 1

    @property
    def items(self) -> tuple[AttachmentRef, ...]:
        return self._items

    @property
    def image_tokens(self) -> tuple[tuple[str, str], ...]:
        return tuple((item.sha256, f"[image{self._image_numbers[item.sha256]}]")
                     for item in self._items if item.media_type.startswith("image/"))

    def validate(self, items: Sequence[AttachmentRef] | None = None) -> None:
        checked = freeze_attachments(self._items if items is None else tuple(items))
        if self._validate is not None:
            self._validate(checked)

    async def add_paths(self, paths: Sequence[str]) -> tuple[AttachmentRef, ...]:
        checked = tuple(paths)
        added = await asyncio.to_thread(
            self._ingestor.ingest_paths,
            checked,
            explicit_external=True,
        )
        self._replace((*self._items, *added))
        return tuple(added)

    async def add_clipboard(self) -> AttachmentRef:
        added = await asyncio.to_thread(self._ingestor.ingest_clipboard)
        self._replace((*self._items, added))
        return added

    async def add_clipboard_items(self) -> tuple[AttachmentRef, ...]:
        loader = getattr(self._ingestor, "ingest_clipboard_items", None)
        if loader is None:
            raise RuntimeError(
                "attachment ingestor does not support atomic clipboard batches"
            )
        remaining_count = MAX_MESSAGE_ATTACHMENTS - len(self._items)
        remaining_bytes = MAX_MESSAGE_ATTACHMENT_BYTES - sum(
            item.size_bytes for item in self._items
        )
        added = tuple(
            await asyncio.to_thread(
                loader,
                max_attachments=remaining_count,
                max_total_bytes=remaining_bytes,
            )
        )
        if not added:
            raise ValueError("clipboard does not contain images")
        self._replace((*self._items, *added))
        return added

    def remove(self, selector: str) -> AttachmentRef:
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError("attachment selector is required")
        query = selector.strip()
        named = {label.strip("[]"): identifier for identifier, label in self.image_tokens}
        query = named.get(query.strip("[]").casefold(), query)
        index = _selected_index(self._items, query)
        removed = self._items[index]
        self._items = self._items[:index] + self._items[index + 1 :]
        self._forget_images()
        return removed

    def clear(self) -> None:
        self._items = ()
        self._forget_images()

    def commit(self, items: Sequence[AttachmentRef]) -> None:
        """Remove only references proven durable by the completed submission."""
        committed = {item.sha256 for item in freeze_attachments(tuple(items))}
        self._items = tuple(
            item for item in self._items if item.sha256 not in committed
        )
        self._forget_images()

    def rows(self) -> tuple[str, ...]:
        if not self._items:
            return ("attachments: none",)
        labels = dict(self.image_tokens)
        return tuple(
            f"{index}. " + (labels[item.sha256] + " " if item.sha256 in labels else "") + item.summary()
            for index, item in enumerate(self._items, start=1)
        )

    def _replace(self, items: Sequence[AttachmentRef]) -> None:
        unique = {item.sha256: item for item in items}
        self._items = freeze_attachments(tuple(unique.values()))
        for item in self._items:
            if item.media_type.startswith("image/") and item.sha256 not in self._image_numbers:
                self._image_numbers[item.sha256] = self._next_image_number
                self._next_image_number += 1

    def _forget_images(self) -> None:
        present = {item.sha256 for item in self._items}
        self._image_numbers = {key: value for key, value in self._image_numbers.items() if key in present}
        if not self._items:
            self._next_image_number = 1


def prepare_input(
    draft: AttachmentDraft | None,
    text: str,
    supplied: Sequence[AttachmentRef] | None,
) -> PreparedInput:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    from_draft = supplied is None
    items = freeze_attachments(
        draft.items if from_draft and draft is not None else tuple(supplied or ())
    )
    if not text.strip() and not items:
        raise ValueError("text and attachments must not both be blank")
    if draft is not None:
        draft.validate(items)
    prompt = text if text.strip() else DEFAULT_ATTACHMENT_PROMPT
    display = text if text.strip() else "Attached input"
    if items:
        summaries = "\n".join(f"- {item.summary()}" for item in items)
        display += "\nAttachments:\n" + summaries
    return PreparedInput(prompt, display, items, from_draft)


def has_submission_input(
    draft: AttachmentDraft | None,
    text: str,
    supplied: Sequence[AttachmentRef] | None,
) -> bool:
    items = draft.items if supplied is None and draft is not None else tuple(supplied or ())
    return bool(text.strip() or items)


def dropped_file_paths(value: str) -> tuple[str, ...]:
    """Recognize a complete Windows Terminal file-drop paste, never partial text."""
    if not isinstance(value, str):
        raise TypeError("pasted value must be a string")
    compact = value.strip()
    if not compact or "\n" in compact or "\r" in compact:
        return ()
    try:
        tokens = attachment_path_arguments(compact)
    except ValueError:
        return ()
    if not all(_drop_candidate(token) for token in tokens):
        return ()
    return tokens


def attachment_path_arguments(value: str) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise TypeError("attachment path arguments must be a string")
    try:
        raw = tuple(shlex.split(value, posix=False))
    except ValueError:
        raise ValueError("attachment paths contain invalid quoting") from None
    result = tuple(_unquote(item) for item in raw)
    if not result or any(not item or '"' in item for item in result):
        raise ValueError("at least one valid attachment path is required")
    return result


def _drop_candidate(value: str) -> bool:
    path = Path(value)
    windows = PureWindowsPath(value)
    return bool(
        windows.is_absolute()
        and path.suffix.casefold() in _SUPPORTED_SUFFIXES
        and path.is_file()
    )


def _selected_index(items: tuple[AttachmentRef, ...], query: str) -> int:
    if query.isdecimal():
        index = int(query) - 1
        if 0 <= index < len(items):
            return index
    matches = [
        index for index, item in enumerate(items) if item.sha256.startswith(query.casefold())
    ]
    if len(matches) != 1:
        raise ValueError("attachment selector is unknown or ambiguous")
    return matches[0]


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
