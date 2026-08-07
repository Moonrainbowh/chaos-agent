from __future__ import annotations

from .i18n import Language
from .terminal_renderer import Theme


def status_presentation(
    status: str, summary: str, action: str | None, language: Language, theme: Theme, spinner_index: int
) -> tuple[str, str, str | None]:
    symbols = theme is Theme.SYMBOL
    if status in {
        "building_context",
        "waiting_model",
        "reasoning",
        "streaming_response",
        "preparing_action",
    }:
        labels = {
            "building_context": ("正在准备工作区", "preparing workspace"),
            "waiting_model": ("正在等待模型", "waiting for model"),
            "reasoning": ("正在分析", "analyzing"),
            "streaming_response": ("正在生成回复", "streaming response"),
            "preparing_action": ("正在准备工具", "preparing tool"),
        }
        zh, en = labels[status]
        if status == "preparing_action" and action:
            zh, en = f"正在准备 · {action}", f"Preparing · {action}"
        spinner = "◐◓◑◒" if symbols else "|/-\\"
        return (
            zh if language is Language.ZH_CN else en,
            spinner[spinner_index % len(spinner)],
            "38;5;250",
        )
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
    labels = {
        "created": ("已创建", "created", "·", None),
        "verifying": ("验证中", "verifying", "◆", "38;5;80"),
        "paused": ("已暂停", "paused", "!", "33"),
        "interrupted": ("已中断", "interrupted", "!", "33"),
        "waiting_decision": ("等待决定", "waiting decision", "?", "33"),
        "approval": ("等待审批", "approval required", "?", "33"),
        "accepted_partial": ("部分交付", "partial delivery", "!", "33"),
        "failed": ("任务失败", "task failed", "×", "31"),
    }
    if status in labels:
        zh, en, symbol, color = labels[status]
        ascii_symbol = {"◆": "*", "×": "x", "·": "."}.get(symbol, symbol)
        return (zh if language is Language.ZH_CN else en), symbol if symbols else ascii_symbol, color
    return ("就绪" if language is Language.ZH_CN else "ready"), "·" if symbols else ".", None


def status_context(
    model: str | None,
    started_at: float | None,
    now: float,
    token_rate: float | None = None,
) -> str:
    parts = [model] if model else []
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
    return " · ".join(parts)
