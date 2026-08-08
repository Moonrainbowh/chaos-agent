from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from code_agent.policy.models import ApprovalMode


_DESCRIPTIONS = {
    ApprovalMode.UNRESTRICTED: "高信任访问，受保护路径仍需审批",
    ApprovalMode.PLAN: "仅允许工作区只读操作",
    ApprovalMode.ASK: "写入和命令逐次审批",
    ApprovalMode.AUTO: "普通读写自动执行，命令审批",
    ApprovalMode.ELEVATED: "外部访问走审批，类型化文件仍限工作区",
    ApprovalMode.FULL_LOCAL: "本地高信任策略，类型化文件仍限工作区",
}


@dataclass(frozen=True)
class PermissionModeSummary:
    name: str
    description: str


class PermissionControl:
    """Switch the central approval policy only at an idle task boundary."""

    def __init__(
        self,
        current: ApprovalMode,
        apply: Callable[[ApprovalMode], Awaitable[None]],
    ) -> None:
        self._current = ApprovalMode(current)
        self._apply = apply

    @property
    def current(self) -> PermissionModeSummary:
        return _summary(self._current)

    def list(self) -> tuple[PermissionModeSummary, ...]:
        return tuple(_summary(mode) for mode in ApprovalMode)

    async def use(self, name: str, *, idle: bool) -> PermissionModeSummary:
        if not idle:
            raise RuntimeError("permission switching is available only when idle")
        try:
            selected = ApprovalMode(name)
        except ValueError:
            raise ValueError("unknown permission mode") from None
        await self._apply(selected)
        self._current = selected
        return _summary(selected)


def _summary(mode: ApprovalMode) -> PermissionModeSummary:
    return PermissionModeSummary(mode.value, _DESCRIPTIONS[mode])
