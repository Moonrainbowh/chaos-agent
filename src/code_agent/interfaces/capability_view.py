from __future__ import annotations

from dataclasses import dataclass

from code_agent.orchestration.models import ModeSnapshot
from code_agent.policy.models import ApprovalMode


@dataclass(frozen=True)
class PermissionSummary:
    approval_mode: ApprovalMode
    workspace_root: str
    allow_network: bool
    allow_workspace_write: bool

    def __post_init__(self) -> None:
        if not isinstance(self.approval_mode, ApprovalMode):
            raise TypeError("approval_mode must be ApprovalMode")
        if not isinstance(self.workspace_root, str) or not self.workspace_root.strip():
            raise ValueError("workspace_root must be non-blank")
        if not isinstance(self.allow_network, bool) or not isinstance(self.allow_workspace_write, bool):
            raise TypeError("permission flags must be bool")


@dataclass(frozen=True)
class ModePermissionView:
    mode: ModeSnapshot
    permission: PermissionSummary
    applies_next_task: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ModeSnapshot):
            raise TypeError("mode must be ModeSnapshot")
        if not isinstance(self.permission, PermissionSummary):
            raise TypeError("permission must be PermissionSummary")

    def lines(self) -> tuple[str, ...]:
        definition = self.mode.definition
        oracle = self.mode.oracle_model or "none"
        boundary = "next task" if self.applies_next_task else "current task"
        return (
            f"mode: {definition.mode.value} · model {self.mode.model} · oracle {oracle}",
            f"reasoning: {definition.reasoning_effort.value} · tools {len(definition.tool_names)} · applies {boundary}",
            f"permission: {self.permission.approval_mode.value} · workspace {self.permission.workspace_root}",
            f"access: write {'yes' if self.permission.allow_workspace_write else 'no'} · network {'yes' if self.permission.allow_network else 'no'}",
        )
