from __future__ import annotations

from collections.abc import Mapping

from .models import ActionRequest


def validation_fingerprint(request: ActionRequest, result: object) -> str | None:
    """Return a stable validation-failure identity for loop supervision."""
    output = getattr(result, "output", {})
    if not isinstance(output, Mapping) or (
        not getattr(result, "is_error", True)
        and output.get("returncode") in {0, None}
    ):
        return None
    if request.name == "run_verification":
        kind = request.arguments.get("kind")
        if not isinstance(kind, str):
            return None
        subject = "|".join(
            (
                kind,
                str(request.arguments.get("cwd", ".")),
                repr(request.arguments.get("targets", ())),
            )
        )
    else:
        subject = request.arguments.get("command")
    if not isinstance(subject, str):
        return None
    metadata = getattr(result, "metadata", {})
    digest = metadata.get("failure_fingerprint") if isinstance(metadata, Mapping) else None
    if (
        isinstance(digest, str)
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    ):
        return f"{subject[:120]}|{output.get('reason', 'failed')}|{digest}"
    prefix = " ".join(
        str(output.get(key, ""))[:256] for key in ("stdout", "stderr")
    )
    return (
        f"{subject[:120]}|{output.get('returncode')}|"
        f"{output.get('reason', 'failed')}|{prefix[:256]}"
    )
