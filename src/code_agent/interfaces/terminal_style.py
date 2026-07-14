from __future__ import annotations

import os
from enum import Enum


class ColorMode(str, Enum):
    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


BRAND_CYAN = "38;5;80"
BRIGHT_CYAN = "1;96"
DIM_GRAY = "2;38;5;245"
TOOL_GRAY = "38;5;250"
BORDER_GRAY = "38;5;240"
BODY_WHITE = "38;5;252"
SUCCESS_GREEN = "38;5;114"
WARNING_YELLOW = "33"
ERROR_RED = "31"


def color_enabled(mode: ColorMode, env: dict[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return mode is ColorMode.ALWAYS or (mode is ColorMode.AUTO and not source.get("NO_COLOR"))


def colorize(value: str, code: str | None, mode: ColorMode) -> str:
    if not value or not code or not color_enabled(mode):
        return value
    return f"\x1b[{code}m{value}\x1b[0m"
