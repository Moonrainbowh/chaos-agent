from __future__ import annotations

import io
import os
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from code_agent.core.attachments import (
    MAX_MESSAGE_ATTACHMENTS, MAX_MESSAGE_ATTACHMENT_BYTES, AttachmentRef,
)
from code_agent.workspace._guarded_read import read_guarded_file
from code_agent.workspace.errors import PathOutsideWorkspace, WorkspaceError
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard

from .clipboard import read_clipboard_payload
from .errors import AttachmentError
from .limits import batch_limit
from .security import read_external
from .store import AttachmentStore


_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"})
_TEXT_SUFFIXES = frozenset(
    {
        ".txt", ".md", ".rst", ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx",
        ".json", ".toml", ".yaml", ".yml", ".xml", ".html", ".css", ".scss",
        ".c", ".h", ".cpp", ".hpp", ".cs", ".java", ".go", ".rs", ".sh",
        ".ps1", ".sql", ".ini", ".cfg", ".csv", ".tsv", ".log",
    }
)


@dataclass(frozen=True)
class AttachmentLimits:
    max_attachments: int = MAX_MESSAGE_ATTACHMENTS
    max_total_bytes: int = MAX_MESSAGE_ATTACHMENT_BYTES
    max_image_input_bytes: int = 16 * 1024 * 1024
    max_image_output_bytes: int = 16 * 1024 * 1024
    max_text_bytes: int = 1024 * 1024
    max_pixels: int = 25_000_000

    def __post_init__(self) -> None:
        for name in (
            "max_attachments", "max_total_bytes", "max_image_input_bytes",
            "max_image_output_bytes", "max_text_bytes", "max_pixels",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_attachments > MAX_MESSAGE_ATTACHMENTS:
            raise ValueError("max_attachments exceeds Message capacity")
        if self.max_total_bytes > MAX_MESSAGE_ATTACHMENT_BYTES:
            raise ValueError("max_total_bytes exceeds Message capacity")


class AttachmentIngestor:
    def __init__(
        self,
        store: AttachmentStore,
        *,
        workspace_guard: WorkspacePathGuard | None = None,
        ignore_rules: IgnoreRules | None = None,
        limits: AttachmentLimits | None = None,
    ) -> None:
        if not isinstance(store, AttachmentStore):
            raise TypeError("store must be an AttachmentStore")
        if workspace_guard is not None and not isinstance(
            workspace_guard, WorkspacePathGuard
        ):
            raise TypeError("workspace_guard must be a WorkspacePathGuard")
        if ignore_rules is not None and not isinstance(ignore_rules, IgnoreRules):
            raise TypeError("ignore_rules must be IgnoreRules")
        self.store = store
        self.guard = workspace_guard
        self.ignore = ignore_rules
        self.limits = limits or AttachmentLimits()

    def ingest_path(
        self, path: str | os.PathLike[str], *, explicit_external: bool = False
    ) -> AttachmentRef:
        source = Path(path)
        kind = _source_kind(source.name, None)
        maximum = (
            self.limits.max_image_input_bytes
            if kind == "image"
            else self.limits.max_text_bytes
        )
        data, display_name = self._read_path(
            source, maximum, explicit_external=explicit_external
        )
        return self.ingest_bytes(data, display_name=display_name, media_type=kind)

    def ingest_paths(
        self,
        paths: Sequence[str | os.PathLike[str]],
        *,
        explicit_external: bool = False,
        max_attachments: int | None = None,
        max_total_bytes: int | None = None,
    ) -> tuple[AttachmentRef, ...]:
        checked = tuple(paths)
        if not checked:
            raise AttachmentError("at least one attachment path is required")
        count_limit = batch_limit(max_attachments, self.limits.max_attachments, "max_attachments")
        byte_limit = batch_limit(max_total_bytes, self.limits.max_total_bytes, "max_total_bytes")
        if len(checked) > count_limit:
            raise AttachmentError("too many attachments")
        with tempfile.TemporaryDirectory() as directory:
            staging = AttachmentStore(Path(directory))
            staged_ingestor = AttachmentIngestor(
                staging,
                workspace_guard=self.guard,
                ignore_rules=self.ignore,
                limits=self.limits,
            )
            references = tuple(
                staged_ingestor.ingest_path(
                    path, explicit_external=explicit_external
                )
                for path in checked
            )
            if sum(item.size_bytes for item in references) > byte_limit:
                raise AttachmentError("attachments exceed the total byte limit")
            return tuple(
                self.store.put(
                    staging.read(item),
                    media_type=item.media_type,
                    display_name=item.display_name,
                    width=item.width,
                    height=item.height,
                )
                for item in references
            )

    def ingest_bytes(
        self,
        data: bytes,
        *,
        display_name: str,
        media_type: str | None = None,
    ) -> AttachmentRef:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        kind = _source_kind(display_name, media_type)
        if kind == "image":
            normalized, width, height = self._normalize_image(data)
            return self.store.put(
                normalized,
                media_type="image/png",
                display_name=_png_name(display_name),
                width=width,
                height=height,
            )
        normalized_text = self._normalize_text(data)
        return self.store.put(
            normalized_text, media_type="text/plain", display_name=display_name
        )

    def ingest_clipboard(self) -> AttachmentRef:
        payload = read_clipboard_payload(self.limits.max_pixels)
        if not isinstance(payload, bytes):
            raise AttachmentError("clipboard does not contain one image")
        return self.ingest_bytes(payload, display_name="clipboard.png", media_type="image")

    def ingest_clipboard_items(
        self,
        *,
        max_attachments: int | None = None,
        max_total_bytes: int | None = None,
    ) -> tuple[AttachmentRef, ...]:
        count_limit = batch_limit(max_attachments, self.limits.max_attachments, "max_attachments")
        byte_limit = batch_limit(max_total_bytes, self.limits.max_total_bytes, "max_total_bytes")
        if count_limit == 0:
            raise AttachmentError("too many attachments")
        if byte_limit == 0:
            raise AttachmentError("attachments exceed the total byte limit")
        payload = read_clipboard_payload(self.limits.max_pixels)
        if isinstance(payload, bytes):
            normalized, width, height = self._normalize_image(payload)
            if len(normalized) > byte_limit:
                raise AttachmentError("attachments exceed the total byte limit")
            reference = self.store.put(
                normalized, media_type="image/png", display_name="clipboard.png",
                width=width, height=height,
            )
            return (reference,)
        try:
            images_only = all(
                _source_kind(Path(path).name, None) == "image" for path in payload
            )
        except AttachmentError:
            images_only = False
        if not images_only:
            raise AttachmentError("clipboard file list must contain supported images")
        return self.ingest_paths(
            payload,
            explicit_external=True,
            max_attachments=count_limit,
            max_total_bytes=byte_limit,
        )

    def _read_path(
        self, path: Path, maximum: int, *, explicit_external: bool
    ) -> tuple[bytes, str]:
        if self.guard is None:
            if not explicit_external:
                raise AttachmentError("path ingestion requires a workspace guard")
            return read_external(path, maximum)
        try:
            resolved = self.guard.resolve(path)
            relative = resolved.relative_to(self.guard.root)
        except PathOutsideWorkspace:
            if not explicit_external:
                raise AttachmentError("attachment path is outside the workspace") from None
            return read_external(path, maximum)
        except WorkspaceError:
            raise AttachmentError("attachment path is not allowed") from None
        if self.ignore is not None and self.ignore.is_ignored(relative):
            raise AttachmentError("ignored workspace files cannot be attached")
        try:
            data = read_guarded_file(path, self.guard, maximum, reject_known_oversize=True)
        except WorkspaceError:
            raise AttachmentError("workspace attachment cannot be read") from None
        return data, resolved.name

    def _normalize_image(self, data: bytes) -> tuple[bytes, int, int]:
        if not data or len(data) > self.limits.max_image_input_bytes:
            raise AttachmentError("image exceeds its input byte limit")
        try:
            from PIL import Image, ImageOps

            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as probe:
                    frames = getattr(probe, "n_frames", 1)
                    if getattr(probe, "is_animated", False) or frames != 1:
                        raise AttachmentError("animated images are not supported")
                    width, height = probe.size
                    if width <= 0 or height <= 0 or width * height > self.limits.max_pixels:
                        raise AttachmentError("image exceeds its pixel limit")
                    probe.verify()
                with Image.open(io.BytesIO(data)) as opened:
                    image = ImageOps.exif_transpose(opened)
                    image.load()
                    alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
                    normalized = image.convert("RGBA" if alpha else "RGB")
                    width, height = normalized.size
                    output = io.BytesIO()
                    normalized.save(output, format="PNG", compress_level=9)
        except AttachmentError:
            raise
        except Exception:
            raise AttachmentError("attachment is not a valid supported image") from None
        result = output.getvalue()
        if not result or len(result) > self.limits.max_image_output_bytes:
            raise AttachmentError("normalized image exceeds its byte limit")
        return result, width, height

    def _normalize_text(self, data: bytes) -> bytes:
        if not data or len(data) > self.limits.max_text_bytes or b"\0" in data:
            raise AttachmentError("text attachment exceeds limits or contains NUL")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise AttachmentError("text attachment must be valid UTF-8") from None
        if any(ord(char) < 32 and char not in "\t\r\n" for char in text):
            raise AttachmentError("text attachment contains binary control bytes")
        return text.encode("utf-8")


def _source_kind(display_name: str, media_type: str | None) -> str:
    if media_type in {"image", "image/png", "image/jpeg", "image/webp", "image/bmp", "image/gif"}:
        return "image"
    if media_type in {"text", "text/plain"}:
        return "text"
    if media_type is not None:
        raise AttachmentError("attachment media type is not supported")
    suffix = Path(display_name).suffix.casefold()
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    if suffix in _TEXT_SUFFIXES:
        return "text"
    raise AttachmentError("attachment file extension is not supported")


def _png_name(name: str) -> str:
    path = Path(name)
    return f"{path.stem or 'image'}.png"
