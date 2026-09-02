from __future__ import annotations

from collections.abc import Mapping, Sequence

from .approval import ApprovalRequest
from .terminal_display import clip_display, safe_text


def approval_card_rows(
    request: ApprovalRequest, selected: int
) -> tuple[str, ...]:
    rows = [
        "需要审批",
        f"动作: {_line(request.name)}",
        f"风险: {_line(request.risk or 'unknown')}",
        f"目标: {_approval_target(request)}",
    ]
    if request.reason:
        rows.append(f"原因: {_line(request.reason)}")
    rows.extend(
        (
            ("› " if selected == 0 else "  ") + "拒绝",
            ("› " if selected == 1 else "  ") + "仅允许这一次",
            "Enter 选择 · Esc 拒绝",
        )
    )
    return tuple(rows)


def _approval_target(request: ApprovalRequest) -> str:
    arguments = request.arguments
    command = arguments.get("command")
    if isinstance(command, str) and command.strip():
        return _line(command)
    program = arguments.get("program")
    args = arguments.get("args")
    if isinstance(program, str) and program.strip():
        values = [program]
        if isinstance(args, Sequence) and not isinstance(args, (str, bytes)):
            values.extend(str(item) for item in args if isinstance(item, str))
        return _line(" ".join(values))
    value = _first_text(
        arguments,
        ("path", "source", "destination", "target", "url", "cwd"),
    )
    return _line(value or request.target or request.name)


def _first_text(arguments: Mapping[str, object], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _line(value: str) -> str:
    return clip_display(safe_text(value).replace("\n", " ").strip(), 180)
