from __future__ import annotations

from collections.abc import Callable

from code_agent.core.attachments import AttachmentRef
from code_agent.interfaces.attachment_input import AttachmentDraft
from code_agent.providers.config import InputModality, ModelProfile


def build_attachment_draft(
    ingestor: object,
    profile_supplier: Callable[[], ModelProfile],
) -> AttachmentDraft:
    if not callable(profile_supplier):
        raise TypeError("profile supplier must be callable")

    def validate(items: tuple[AttachmentRef, ...]) -> None:
        profile = profile_supplier()
        if not isinstance(profile, ModelProfile):
            raise RuntimeError("current model profile is unavailable")
        has_image = any(item.media_type.startswith("image/") for item in items)
        if has_image and InputModality.IMAGE not in profile.input_modalities:
            raise RuntimeError("current model does not support image input")

    return AttachmentDraft(ingestor, validate=validate)


__all__ = ["build_attachment_draft"]
