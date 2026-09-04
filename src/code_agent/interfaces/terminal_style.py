from __future__ import annotations

import os
from enum import Enum


class ColorMode(str, Enum):
    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


BRAND_CYAN = "38;5;117"
BRIGHT_CYAN = "1;38;5;117"
DIM_GRAY = "38;5;243"
TOOL_GRAY = "38;5;248"
BORDER_GRAY = "38;5;241"
BODY_WHITE = "38;5;252"
SUCCESS_GREEN = "38;5;115"
WARNING_YELLOW = "38;5;179"
ERROR_RED = "38;5;203"


def color_enabled(mode: ColorMode, env: dict[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return mode is ColorMode.ALWAYS or (mode is ColorMode.AUTO and not source.get("NO_COLOR"))


def colorize(value: str, code: str | None, mode: ColorMode) -> str:
    if not value or not code or not color_enabled(mode):
        return value
    return f"\x1b[{code}m{value}\x1b[0m"
