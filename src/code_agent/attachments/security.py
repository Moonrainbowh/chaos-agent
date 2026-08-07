from __future__ import annotations

import os
import stat
from pathlib import Path

from .errors import AttachmentError


_PRIVATE_NAMES = frozenset(
    {"id_dsa", "id_ecdsa", "id_ed25519", "id_rsa", "private-key", "private_key"}
)
_PRIVATE_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".private")


def reject_sensitive_external(path: Path) -> None:
    name = path.name.casefold()
    env_file = name == ".env" or (
        name.startswith(".env.")
        and not name.endswith((".example", ".sample", ".template"))
    )
    private = (
        name in _PRIVATE_NAMES
        or name.endswith(_PRIVATE_SUFFIXES)
        or (name.startswith("ssh_host_") and name.endswith("_key"))
        or any(marker in name for marker in ("credential", "secret", "api_key"))
    )
    if env_file or private:
        raise AttachmentError("sensitive external files cannot be attached")
    local = Path(
        os.getenv("LOCALAPPDATA")
        or str(Path.home() / "AppData" / "Local")
    )
    protected = (local / "chaos-agent", local / "code-agent")
    configured = os.getenv("CHAOS_CONFIG") or os.getenv("CODE_AGENT_CONFIG")
    if configured and Path(configured).is_absolute():
        protected += (Path(configured).expanduser().parent,)
    for directory in protected:
        try:
            path.relative_to(directory.resolve(strict=False))
        except ValueError:
            continue
        raise AttachmentError("local product configuration cannot be attached")


def read_external(path: Path, maximum: int) -> tuple[bytes, str]:
    if not path.is_absolute():
        raise AttachmentError("external attachments require an explicit absolute path")
    try:
        resolved = path.resolve(strict=True)
        reject_sensitive_external(resolved)
        _reject_link_components(path)
        metadata = resolved.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
            raise AttachmentError("external attachment is not a bounded regular file")
        with resolved.open("rb") as stream:
            data = stream.read(maximum + 1)
    except AttachmentError:
        raise
    except OSError:
        raise AttachmentError("external attachment cannot be read") from None
    if len(data) > maximum:
        raise AttachmentError("external attachment exceeds its byte limit")
    return data, resolved.name


def _reject_link_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        metadata = current.lstat()
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if current.is_symlink() or getattr(metadata, "st_file_attributes", 0) & reparse:
            raise AttachmentError("linked external attachment paths are not allowed")
