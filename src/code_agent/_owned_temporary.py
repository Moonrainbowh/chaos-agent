from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


_FileIdentity = tuple[int, int, int]

if os.name == "nt":
    from . import _windows_owned_temporary as _windows


class OwnedTemporaryReplacedError(OSError):
    """The path no longer names the temporary file created by this operation."""


@dataclass(frozen=True)
class OwnedTemporary:
    path: Path
    identity: _FileIdentity
    parent_identity: _FileIdentity

    @classmethod
    def capture(cls, path: Path) -> OwnedTemporary:
        checked = Path(path)
        return cls(
            checked,
            _file_identity(checked),
            _file_identity(checked.parent),
        )

    @classmethod
    def capture_descriptor(cls, path: Path, descriptor: int) -> OwnedTemporary:
        checked = Path(path)
        return cls(
            checked,
            _identity_from_stat(os.fstat(descriptor)),
            _file_identity(checked.parent),
        )

    @classmethod
    def capture_cleanup_descriptor(
        cls, path: Path, descriptor: int
    ) -> OwnedTemporary:
        """Capture only the exact file identity needed for failure cleanup."""
        checked = Path(path)
        return cls(
            checked,
            _identity_from_stat(os.fstat(descriptor)),
            (0, 0, 0),
        )

    def cleanup(self) -> None:
        if os.name == "nt":
            _windows.cleanup_exact(
                self.path,
                self.identity,
                OwnedTemporaryReplacedError,
            )
            return
        try:
            current = _file_identity(self.path)
        except FileNotFoundError:
            return
        if current != self.identity:
            raise OwnedTemporaryReplacedError(
                "owned temporary path was replaced; foreign file was preserved"
            )
        self.path.unlink()

    def publish_no_replace(
        self,
        destination: Path,
        verifier: Callable[[Path], None],
    ) -> bool:
        """Publish this exact temporary; return whether its source name was consumed."""
        checked = Path(destination)
        if os.name == "nt":
            _windows.publish_no_replace(
                self.path,
                self.identity,
                self.parent_identity,
                checked,
                verifier,
                OwnedTemporaryReplacedError,
            )
            return True
        verifier(self.path)
        os.link(self.path, checked)
        verifier(checked)
        return False


def _file_identity(path: Path) -> _FileIdentity:
    if os.name == "nt":
        return _windows.path_identity(path)
    return _identity_from_stat(path.stat(follow_symlinks=False))


def _identity_from_stat(metadata: os.stat_result) -> _FileIdentity:
    created = metadata.st_ctime_ns
    device = metadata.st_dev
    if os.name == "nt":
        created = created // 100 + 116_444_736_000_000_000
        device &= 0xFFFFFFFF
    return device, metadata.st_ino, created
