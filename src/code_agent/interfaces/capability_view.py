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
    runtime_summary: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ModeSnapshot):
            raise TypeError("mode must be ModeSnapshot")
        if not isinstance(self.permission, PermissionSummary):
            raise TypeError("permission must be PermissionSummary")
        if self.runtime_summary is not None and (
            not isinstance(self.runtime_summary, str)
            or not self.runtime_summary.strip()
        ):
            raise ValueError("runtime_summary must be non-blank text or None")

    def lines(self) -> tuple[str, ...]:
        definition = self.mode.definition
        oracle = self.mode.oracle_model or "none"
        boundary = "next task" if self.applies_next_task else "current task"
        tools = tuple(
            name
            for name in definition.tool_names
            if self.mode.topology.value == "team" or name != "delegate_agent"
        )
        reasoning = self.mode.effective_reasoning_effort
        selection = self.mode.runtime_selection
        if selection is not None and selection.api_protocol == "anthropic_messages":
            reasoning += " (prompt-only; structured effort unsupported)"
        lines = (
            f"mode: {definition.mode.value} · model {self.mode.model} · oracle {oracle} · topology {self.mode.topology.value}",
            f"reasoning: {reasoning} · tools {len(tools)} · applies {boundary}",
            f"permission: {self.permission.approval_mode.value} · workspace {self.permission.workspace_root}",
            f"access: write {'yes' if self.permission.allow_workspace_write else 'no'} · network {'yes' if self.permission.allow_network else 'no'}",
        )
        if self.runtime_summary is None:
            return lines
        return lines + (f"runtime: {self.runtime_summary}",)
