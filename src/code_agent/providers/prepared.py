"""Immutable HTTP body shared by admission, serialization and transport retries."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PreparedProviderRequest:
    """Headers/URL remain transport-only; input_text is a local token estimate source."""

    body: bytes = field(repr=False)
    url: str = field(repr=False)
    headers: tuple[tuple[str, str], ...] = field(repr=False)
    max_output_tokens: int
    output_limit_enforced: bool = True
    uncalibrated_images: bool = False
    diagnostics: tuple[str, ...] = ()
    _transport_identity: object = field(default=None, repr=False, compare=False)
    _sensitive_values: tuple[str, ...] = field(default=(), repr=False, compare=False)

    @property
    def input_text(self) -> str:
        return self.body.decode("utf-8")


def contains_images(messages) -> bool:
    return any(reference.media_type.startswith("image/")
               for message in messages for reference in message.attachments)
