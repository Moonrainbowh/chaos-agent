from __future__ import annotations

from .i18n import Language
from .terminal_renderer import Theme


def status_presentation(
    status: str, summary: str, action: str | None, language: Language, theme: Theme, spinner_index: int
) -> tuple[str, str, str | None]:
    symbols = theme is Theme.SYMBOL
    if status == "running":
        detail = action or ("正在生成回复" if language is Language.ZH_CN else "generating response")
        prefix = "处理中 · " if language is Language.ZH_CN else "Working · "
        spinner = "◐◓◑◒" if symbols else "|/-\\"
        return prefix + detail, spinner[spinner_index % len(spinner)], "38;5;250"
    if status == "completed":
        label = summary or ("已完成" if language is Language.ZH_CN else "completed")
        return label, "✓" if symbols else "+", "38;5;114"
    if status == "error":
        return ("处理失败" if language is Language.ZH_CN else "failed"), "×" if symbols else "x", "31"
    if status == "cancelled":
        return ("已取消" if language is Language.ZH_CN else "cancelled"), "!", "33"
    return ("就绪" if language is Language.ZH_CN else "ready"), "·" if symbols else ".", None


def status_context(model: str | None, started_at: float | None, now: float) -> str:
    parts = [model] if model else []
    if started_at is not None:
        elapsed = int(now - started_at)
        parts.append(f"{elapsed // 60:02d}:{elapsed % 60:02d}")
    return " · ".join(parts)
