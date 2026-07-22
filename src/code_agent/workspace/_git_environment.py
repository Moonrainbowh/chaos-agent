from __future__ import annotations

import os
from collections.abc import Mapping


_PRESERVED_KEYS = (
    "PATH",
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
)


def isolated_git_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build a noninteractive child environment without repository bindings."""
    source = os.environ if source is None else source
    child = {key: source[key] for key in _PRESERVED_KEYS if key in source}
    child.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    return child
