from __future__ import annotations

import io

from .errors import AttachmentError


ClipboardPayload = bytes | tuple[str, ...]


def read_clipboard_payload(max_pixels: int) -> ClipboardPayload:
    """Return one encoded bitmap or a copied Windows file list."""
    try:
        from PIL import Image, ImageGrab

        value = ImageGrab.grabclipboard()
    except Exception:
        raise AttachmentError("clipboard image is unavailable") from None
    if isinstance(value, Image.Image):
        return _encode_bitmap(value, max_pixels)
    if isinstance(value, list) and value and all(
        isinstance(path, str) and path for path in value
    ):
        return tuple(value)
    raise AttachmentError("clipboard does not contain an image or image files")


def _encode_bitmap(image: object, max_pixels: int) -> bytes:
    if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) != 1:
        raise AttachmentError("animated images are not supported")
    width, height = image.size
    if width <= 0 or height <= 0 or width * height > max_pixels:
        raise AttachmentError("image exceeds its pixel limit")
    output = io.BytesIO()
    try:
        image.save(output, format="PNG")
    except Exception:
        raise AttachmentError("clipboard image cannot be encoded") from None
    return output.getvalue()
