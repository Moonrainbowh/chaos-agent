from __future__ import annotations

from code_agent.workflows.models import (
    WorkflowNode,
    WorkflowNodeStatus,
    WorkflowSnapshot,
)

from .terminal_display import clip_display, safe_text


_SYMBOLS = {
    WorkflowNodeStatus.PLANNED: "○",
    WorkflowNodeStatus.QUEUED: "○",
    WorkflowNodeStatus.RUNNING: "●",
    WorkflowNodeStatus.BLOCKED: "○",
    WorkflowNodeStatus.WAITING_DECISION: "!",
    WorkflowNodeStatus.VERIFYING: "●",
    WorkflowNodeStatus.COMPLETED: "✓",
    WorkflowNodeStatus.FAILED: "×",
    WorkflowNodeStatus.CANCELLED: "×",
}
_FAILURE_STATUSES = {
    WorkflowNodeStatus.FAILED,
    WorkflowNodeStatus.BLOCKED,
    WorkflowNodeStatus.WAITING_DECISION,
}


class WorkflowView:
    def render(
        self,
        snapshot: WorkflowSnapshot,
        *,
        width: int,
        filter_name: str | None = None,
    ) -> str:
        _validate(snapshot, width)
        selected = tuple(
            node
            for node in snapshot.nodes
            if filter_name not in {"失败", "failed"}
            or node.status in _FAILURE_STATUSES
        )
        lines = [clip_display(f"任务：{safe_text(snapshot.workflow.title)}", width)]
        if not selected:
            lines.append(clip_display("（没有匹配的流程节点）", width))
            return "\n".join(lines)
        selected_ids = {node.id for node in selected}
        parents = {
            edge.target_node_id: edge.source_node_id
            for edge in snapshot.edges
            if edge.target_node_id in selected_ids
            and edge.source_node_id in selected_ids
        }
        children: dict[str, list[str]] = {}
        for child, parent in parents.items():
            children.setdefault(parent, []).append(child)
        by_id = {node.id: node for node in selected}
        roots = [node.id for node in selected if node.id not in parents]
        for index, node_id in enumerate(roots):
            self._append_tree(
                lines,
                by_id,
                children,
                node_id,
                "",
                index == len(roots) - 1,
                width,
                root=True,
            )
        return "\n".join(lines)

    def detail(
        self, snapshot: WorkflowSnapshot, node_id: str, *, width: int
    ) -> str:
        _validate(snapshot, width)
        node = _find(snapshot, node_id)
        dependencies = tuple(
            edge.source_node_id
            for edge in snapshot.edges
            if edge.target_node_id == node.id
        )
        rows = (
            f"节点 {safe_text(node.id)} · {safe_text(node.title)}",
            f"状态：{node.status.value}",
            f"角色：{safe_text(node.role)}",
            f"线程：{safe_text(node.assigned_thread_id or '无')}",
            f"依赖：{', '.join(map(safe_text, dependencies)) or '无'}",
            f"输入：{', '.join(map(safe_text, node.input_refs)) or '无'}",
            f"输出：{', '.join(map(safe_text, node.output_refs)) or '无'}",
        )
        return "\n".join(clip_display(row, width) for row in rows)

    def evidence(
        self, snapshot: WorkflowSnapshot, node_id: str, *, width: int
    ) -> str:
        _validate(snapshot, width)
        node = _find(snapshot, node_id)
        value = ", ".join(map(safe_text, node.evidence_refs)) or "无"
        return clip_display(
            f"节点 {safe_text(node.id)} 证据：{value}", width
        )

    def _append_tree(
        self,
        lines: list[str],
        by_id: dict[str, WorkflowNode],
        children: dict[str, list[str]],
        node_id: str,
        prefix: str,
        last: bool,
        width: int,
        *,
        root: bool = False,
    ) -> None:
        node = by_id[node_id]
        branch = "" if root else ("└─" if last else "├─")
        suffix = f"  {safe_text(node.role)}" if width >= 48 else ""
        text = (
            f"{prefix}{branch}{_SYMBOLS[node.status]} "
            f"{safe_text(node.title)}{suffix}"
        )
        lines.append(clip_display(text, width))
        child_prefix = prefix + ("" if root else ("  " if last else "│ "))
        values = children.get(node_id, ())
        for index, child_id in enumerate(values):
            self._append_tree(
                lines,
                by_id,
                children,
                child_id,
                child_prefix,
                index == len(values) - 1,
                width,
            )


def _find(snapshot: WorkflowSnapshot, node_id: str) -> WorkflowNode:
    for node in snapshot.nodes:
        if node.id == node_id:
            return node
    raise KeyError(f"workflow node not found: {safe_text(node_id)}")


def _validate(snapshot: WorkflowSnapshot, width: int) -> None:
    if not isinstance(snapshot, WorkflowSnapshot):
        raise TypeError("snapshot must be WorkflowSnapshot")
    if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
        raise ValueError("width must be positive")
