from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Union

from .errors import PathOutsideWorkspace, SensitivePathError


PathInput = Union[str, os.PathLike[str]]

_PROTECTED_ROOTS = frozenset({".git", ".code-agent"})
_NORMALIZED_PROTECTED = frozenset(os.path.normcase(name) for name in _PROTECTED_ROOTS)
_ENV_EXEMPT_SUFFIXES = (".example", ".sample", ".template")
_PRIVATE_KEY_NAMES = frozenset(
    {
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "private-key",
        "private_key",
    }
)
_PRIVATE_KEY_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".private")


class WorkspacePathGuard:
    """Resolve paths while enforcing containment and sensitive-file policy."""

    def __init__(self, root: PathInput, *, allow_sensitive: bool = False) -> None:
        root_path = Path(root).expanduser()
        if not root_path.exists() or not root_path.is_dir():
            raise ValueError("workspace root must be an existing directory")
        self.root = root_path.resolve(strict=True)
        self.allow_sensitive = bool(allow_sensitive)

    def resolve(self, path: PathInput, *, for_write: bool = False) -> Path:
        """Return a canonical in-workspace path, including for new files."""
        del for_write  # Resolution is equally strict for reads and writes.
        raw = os.fspath(path)
        if not isinstance(raw, str):
            raise TypeError("path must be text")
        if "\0" in raw:
            raise PathOutsideWorkspace("path contains a NUL byte")

        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            lexical_relative = candidate.relative_to(self.root)
        except ValueError as error:
            raise PathOutsideWorkspace(f"path escapes workspace: {raw!r}") from error
        self._reject_link_components(lexical_relative, raw)
        try:
            resolved = candidate.resolve(strict=False)
            relative = resolved.relative_to(self.root)
        except (OSError, RuntimeError, ValueError) as error:
            raise PathOutsideWorkspace(f"path escapes workspace: {raw!r}") from error

        self._check_policy(relative)
        return resolved

    def relative(self, path: PathInput) -> Path:
        """Return the canonical workspace-relative path."""
        return self.resolve(path).relative_to(self.root)

    def _check_policy(self, relative: Path) -> None:
        parts = relative.parts
        if any(os.path.normcase(part) in _NORMALIZED_PROTECTED for part in parts):
            raise SensitivePathError(f"protected workspace path: {relative}")
        if not self.allow_sensitive and parts and _is_sensitive_name(parts[-1]):
            raise SensitivePathError(f"sensitive workspace path: {relative}")

    def _reject_link_components(self, relative: Path, raw: str) -> None:
        current = self.root
        for part in relative.parts:
            if part in ("", "."):
                continue
            if part == "..":
                if current == self.root:
                    raise PathOutsideWorkspace(f"path escapes workspace: {raw!r}")
                current = current.parent
                continue
            current = current / part
            if _is_link_like(current):
                raise PathOutsideWorkspace(f"linked paths are not allowed: {raw!r}")


def _is_sensitive_name(name: str) -> bool:
    lowered = name.casefold()
    if lowered == ".env" or lowered.startswith(".env."):
        return not lowered.endswith(_ENV_EXEMPT_SUFFIXES)
    if lowered in _PRIVATE_KEY_NAMES or lowered.endswith(_PRIVATE_KEY_SUFFIXES):
        return True
    return lowered.startswith("ssh_host_") and lowered.endswith("_key")


def _is_link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)
