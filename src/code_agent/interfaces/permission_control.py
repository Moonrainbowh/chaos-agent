from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from code_agent.policy.command_rules import ProcessRule, ProcessRuleStore
from code_agent.policy.models import ApprovalMode


_DESCRIPTIONS = {
    ApprovalMode.UNRESTRICTED: "高信任访问，受保护路径仍需审批",
    ApprovalMode.PLAN: "仅允许工作区只读操作",
    ApprovalMode.ASK: "写入和命令逐次审批",
    ApprovalMode.AUTO: "工作区读写和已识别本地命令自动执行",
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
        *,
        rules: ProcessRuleStore | None = None,
        workspace_root: Path | None = None,
        workspace_fingerprint: str | None = None,
    ) -> None:
        self._current = ApprovalMode(current)
        self._apply = apply
        self._rules = rules
        self._workspace_root = workspace_root
        self._workspace_fingerprint = workspace_fingerprint

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

    def allow_process(
        self, program: str, args: Sequence[str], *, network: bool = False
    ) -> ProcessRule:
        rules, root, fingerprint = self._rule_context()
        return rules.allow(
            program,
            args,
            workspace_root=root,
            workspace_fingerprint=fingerprint,
            network=network,
        )

    def list_rules(self) -> tuple[ProcessRule, ...]:
        rules, root, fingerprint = self._rule_context()
        return rules.list(
            workspace_root=root, workspace_fingerprint=fingerprint
        )

    def revoke_rule(self, rule_id: str) -> ProcessRule:
        rules, root, fingerprint = self._rule_context()
        return rules.revoke(
            rule_id,
            workspace_root=root,
            workspace_fingerprint=fingerprint,
        )

    def _rule_context(self) -> tuple[ProcessRuleStore, Path, str]:
        if (
            self._rules is None
            or self._workspace_root is None
            or self._workspace_fingerprint is None
        ):
            raise RuntimeError("permanent command rules are unavailable")
        return self._rules, self._workspace_root, self._workspace_fingerprint


def _summary(mode: ApprovalMode) -> PermissionModeSummary:
    return PermissionModeSummary(mode.value, _DESCRIPTIONS[mode])
