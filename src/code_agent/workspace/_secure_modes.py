from __future__ import annotations

import os
import stat

from . import _secure_io as safety


def restore_mode(state: safety.TargetState) -> int:
    if state.identity is None:
        return 0o600
    return stat.S_IMODE(state.identity.mode)


def make_destination_writable(state: safety.TargetState) -> bool:
    if state.identity is None or restore_mode(state) & stat.S_IWRITE:
        return False
    os.chmod(state.target, restore_mode(state) | stat.S_IWRITE)
    return True


def restore_destination_mode(state: safety.TargetState) -> None:
    if state.identity is None:
        return
    try:
        current = safety.identity_from_stat(state.target.lstat())
        if current == state.identity:
            os.chmod(state.target, restore_mode(state))
    except OSError:
        pass
