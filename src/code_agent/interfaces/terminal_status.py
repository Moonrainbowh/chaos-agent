from __future__ import annotations

from .i18n import Language
from .terminal_theme import Theme, design_for, ACTIVE_GOLD
from .terminal_style import (
    BRAND_CYAN,
    ERROR_RED,
    SUCCESS_GREEN,
    TOOL_GRAY,
    WARNING_YELLOW,
)


def status_presentation(
    status: str, summary: str, action: str | None, language: Language, theme: Theme, spinner_index: int
) -> tuple[str, str, str | None]:
    design = design_for(theme)
    symbols = theme in {Theme.SYMBOL, Theme.MODERN} or design is not None
    modern = theme is Theme.MODERN
    active = _activity_status(status, action, language, design, modern, symbols, spinner_index)
    if active is not None:
        return active
    if status == "completed":
        label = summary or ("已完成" if language is Language.ZH_CN else "completed")
        return label, "✓" if symbols else "+", SUCCESS_GREEN
    if status == "error":
        return ("处理失败" if language is Language.ZH_CN else "failed"), "×" if symbols else "x", ERROR_RED
    if status == "cancelled":
        return ("已取消" if language is Language.ZH_CN else "cancelled"), "!", WARNING_YELLOW
    labels = {
        "created": ("已创建", "created", "·", None),
        "verifying": ("验证中", "verifying", "✦" if modern else "◆", BRAND_CYAN),
        "paused": ("已暂停", "paused", "!", WARNING_YELLOW),
        "interrupted": ("已中断", "interrupted", "!", WARNING_YELLOW),
        "waiting_decision": ("等待决定", "waiting for decision", "?", WARNING_YELLOW),
        "approval": ("等待审批", "approval required", "?", WARNING_YELLOW),
        "accepted_partial": ("部分交付", "partial delivery", "!", WARNING_YELLOW),
        "failed": ("任务失败", "task failed", "×", ERROR_RED),
    }
    if status in labels:
        zh, en, symbol, color = labels[status]
        ascii_symbol = {"◆": "*", "✦": "*", "×": "x", "·": "."}.get(symbol, symbol)
        return (zh if language is Language.ZH_CN else en), symbol if symbols else ascii_symbol, color
    if design:
        return ("就绪" if language is Language.ZH_CN else "ready"), "○", design.accent
    ready_icon = "●" if modern else ("·" if symbols else ".")
    ready_color = SUCCESS_GREEN if modern else None
    return "ready", ready_icon, ready_color


def _activity_status(status, action, language, design, modern, symbols, spinner_index):
    if status in {
        "pausing",
        "preparing_workspace",
        "building_context",
        "waiting_model",
        "reasoning",
        "streaming_response",
        "preparing_action",
    }:
        labels = {
            "pausing": ("正在暂停", "pausing"),
            "preparing_workspace": ("正在准备工作区", "preparing workspace"),
            "building_context": ("正在准备上下文", "building context"),
            "waiting_model": ("正在等待模型", "waiting for model"),
            "reasoning": ("正在分析", "analyzing"),
            "streaming_response": ("正在生成回复", "streaming response"),
            "preparing_action": ("正在准备工具", "preparing tool"),
        }
        zh, en = labels[status]
        if status == "preparing_action" and action:
            zh, en = f"正在准备 · {action}", f"Preparing · {action}"
        spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" if modern else ("◐◓◑◒" if symbols else "|/-\\")
        return (
            zh if language is Language.ZH_CN else en,
            _activity_icon(design, spinner, spinner_index),
            ACTIVE_GOLD if design else TOOL_GRAY,
        )
    if status == "running":
        detail = action or "generating response"
        prefix = "Working · "
        spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" if modern else ("◐◓◑◒" if symbols else "|/-\\")
        return prefix + detail, _activity_icon(design, spinner, spinner_index), ACTIVE_GOLD if design else WARNING_YELLOW
    return None


def _activity_icon(design, fallback, tick):
    if design:
        # Fixed width keeps status text still as the bright dot moves.
        return ("●··", "·●·", "··●", "·●·")[(tick // 2) % 4]
    return fallback[tick % len(fallback)]


def status_context(
    model: str | None,
    started_at: float | None,
    now: float,
    token_rate: float | None = None,
    *,
    tokens: int = 0,
    context_window: int | None = None,
    branch: str | None = None,
    show_percentage: bool = True,
    window_number: int | None = None,
    task_spent: int | None = None,
    task_limit: int | None = None,
    task_reserved: int = 0,
) -> str:
    parts = [model] if model else []
    if branch:
        parts.append(branch)
    if tokens > 0:
        window = context_window or 128_000
        pct = max(1, int(tokens * 100 / window))
        formatted_tokens = f"{tokens / 1000:.1f}k" if tokens >= 1000 else str(tokens)
        label = f"window {window_number + 1}: " if window_number is not None else ""
        parts.append(f"{label}{formatted_tokens} tokens ({pct}%)" if show_percentage else f"task {formatted_tokens} tokens")
    if task_spent is not None and task_limit:
        parts.append(f"task {task_spent:,}/{task_limit:,}")
        if task_spent >= task_limit * .8:
            parts.append("task budget >=80%")
        if task_reserved:
            parts.append(f"{task_reserved:,} reserved")
    if token_rate is not None:
        parts.append(f"{token_rate:.1f} token/s")
    if started_at is not None:
        elapsed = int(now - started_at)
        parts.append(f"{elapsed // 60:02d}:{elapsed % 60:02d}")
    return " · ".join(parts)


def status_snapshot(
    status: str,
    task_id: str | None = None,
    thread_id: str | None = None,
    model: str | None = None,
) -> str:
    parts = [f"status: {status}"]
    if task_id:
        parts.append(f"task: {task_id}")
    if thread_id:
        parts.append(f"thread: {thread_id}")
    if model:
        parts.append(f"model: {model}")
    return "  │  ".join(parts)
