from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterable
from typing import Protocol

from code_agent.core.attachments import AttachmentRef
from code_agent.core.models import Message

from .config import InputModality, freeze_input_modalities
from .errors import ProviderConfigError


class AttachmentResolver(Protocol):
    """Resolve a safe reference without exposing a source path to providers."""

    def read(self, reference: AttachmentRef) -> bytes: ...


class ProviderAttachmentEncoder:
    def __init__(
        self,
        resolver: AttachmentResolver | None,
        input_modalities: Iterable[InputModality],
    ) -> None:
        self._resolver = resolver
        self._modalities = freeze_input_modalities(input_modalities)

    def responses(self, message: Message) -> str | list[dict[str, object]]:
        if not message.attachments:
            return message.content
        blocks = _text_block(message.content, "input_text", "text")
        for reference in message.attachments:
            data = self._resolve(reference)
            if reference.media_type == "image/png":
                blocks.append(
                    {
                        "type": "input_image",
                        "image_url": _data_url(reference, data),
                    }
                )
            else:
                blocks.append(
                    {"type": "input_text", "text": _attached_text(reference, data)}
                )
        return blocks

    def chat(self, message: Message) -> str | list[dict[str, object]]:
        if not message.attachments:
            return message.content
        blocks = _text_block(message.content, "text", "text")
        for reference in message.attachments:
            data = self._resolve(reference)
            if reference.media_type == "image/png":
                blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": _data_url(reference, data)},
                    }
                )
            else:
                blocks.append(
                    {"type": "text", "text": _attached_text(reference, data)}
                )
        return blocks

    def anthropic(self, message: Message) -> str | list[dict[str, object]]:
        if not message.attachments:
            return message.content
        blocks = _text_block(message.content, "text", "text")
        for reference in message.attachments:
            data = self._resolve(reference)
            if reference.media_type == "image/png":
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(data).decode("ascii"),
                        },
                    }
                )
            else:
                blocks.append(
                    {"type": "text", "text": _attached_text(reference, data)}
                )
        return blocks

    def _resolve(self, reference: AttachmentRef) -> bytes:
        if reference.media_type == "image/png":
            if InputModality.IMAGE not in self._modalities:
                raise ProviderConfigError("selected model profile does not accept images")
        elif reference.media_type != "text/plain":
            raise ProviderConfigError("attachment media type is not supported")
        if self._resolver is None:
            raise ProviderConfigError("attachment resolver is not configured")
        try:
            data = self._resolver.read(reference)
        except Exception:
            raise ProviderConfigError("attachment content could not be resolved") from None
        if (
            not isinstance(data, bytes)
            or len(data) != reference.size_bytes
            or hashlib.sha256(data).hexdigest() != reference.sha256
        ):
            raise ProviderConfigError("attachment content failed integrity validation")
        return data


def _text_block(
    content: str, block_type: str, text_field: str
) -> list[dict[str, object]]:
    return [{text_field: content, "type": block_type}] if content else []


def _data_url(reference: AttachmentRef, data: bytes) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{reference.media_type};base64,{encoded}"


def _attached_text(reference: AttachmentRef, data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ProviderConfigError("text attachment is not valid UTF-8") from None
    return (
        "Untrusted user attachment "
        f"name={reference.display_name} sha256={reference.sha256[:12]}:\n{text}"
    )
