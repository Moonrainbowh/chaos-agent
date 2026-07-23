from __future__ import annotations


REQUIRED_COLUMNS = {
    "threads": {"id", "created_at", "updated_at", "title", "status", "parent_thread_id"},
    "messages": {"sequence", "thread_id", "payload", "created_at"},
    "events": {"sequence", "thread_id", "payload", "created_at"},
    "goals": {"id", "thread_id", "objective", "status", "metadata", "created_at", "updated_at"},
    "checkpoints": {
        "id", "thread_id", "label", "metadata", "created_at",
        "message_sequence", "event_sequence",
    },
    "task_budgets": {
        "thread_id", "model_name", "max_agent_rounds", "max_tool_calls",
        "max_tool_calls_per_round", "max_total_tokens", "model_turns", "tool_calls",
        "input_tokens", "output_tokens", "repair_cycles", "repeated_failures",
        "last_failure_signature", "active_seconds", "warned_at_80", "warned_at_90",
    },
    "task_states": {"thread_id", "payload", "updated_at"},
    "tasks": {
        "id", "thread_id", "contract", "status", "stop_reason", "created_at",
        "updated_at", "workspace_lineage_id",
    },
    "task_controls": {"sequence", "task_id", "instruction", "created_at"},
    "task_executions": {"task_id", "instance_id", "owner_pid", "owner_create_time", "started_at"},
    "task_contract_revisions": {"task_id", "revision", "payload", "created_at"},
    "verification_runs": {"id", "task_id", "generation", "subject_hash", "status", "created_at", "completed_at"},
    "verification_evidence": {"id", "run_id", "task_id", "payload", "created_at"},
    "task_completions": {"task_id", "revision", "generation", "subject_hash", "assessment", "created_at"},
    "semantic_checkpoints": {"id", "thread_id", "payload", "created_at"},
    "thread_index_entries": {"stable_id", "checkpoint_id", "thread_id", "sequence", "text", "payload", "created_at"},
    "workflows": {"id", "root_thread_id", "task_id", "payload", "created_at", "updated_at"},
    "workflow_nodes": {"id", "workflow_id", "position", "status", "payload"},
    "workflow_edges": {"workflow_id", "source_node_id", "target_node_id", "kind", "position"},
    "skill_activations": {"thread_id", "skill_id", "source", "digest", "activated_at"},
    "workspace_lineages": {
        "id", "repository_id", "source_root", "worktree_root", "branch_name",
        "head_commit", "owner_task_id", "status", "created_at", "updated_at",
    },
    "workspace_snapshots": {"id", "lineage_id", "inventory_digest", "total_bytes", "created_at"},
    "workspace_snapshot_entries": {"snapshot_id", "relative_path", "existed", "blob_sha256", "size", "mode"},
    "checkpoint_workspace_state": {
        "checkpoint_id", "snapshot_id", "lineage_id", "message_sequence", "event_sequence",
        "goals_payload", "task_state_payload", "budget_payload", "snapshot_status",
    },
    "rewind_operations": {
        "id", "lineage_id", "source_checkpoint_id", "rollback_checkpoint_id", "mode",
        "preview_fingerprint", "status", "error_code", "replacement_task_id",
        "created_at", "updated_at",
    },
    "workspace_lineage_usage": {
        "lineage_id", "model_turns", "tool_calls", "input_tokens", "output_tokens",
        "repair_cycles", "repeated_failures", "last_failure_signature",
        "active_seconds", "warned_at_80", "warned_at_90",
    },
}
