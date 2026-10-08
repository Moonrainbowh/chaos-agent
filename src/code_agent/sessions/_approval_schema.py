"""Non-destructive central approval ledger migration."""
APPROVAL_MIGRATION = (
    "CREATE TABLE approval_requests (request_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, thread_id TEXT NOT NULL REFERENCES threads(id), action_id TEXT NOT NULL, action_digest TEXT NOT NULL, workspace_root TEXT NOT NULL, state_version TEXT NOT NULL, owner_instance_id TEXT NOT NULL, preview TEXT NOT NULL CHECK(length(CAST(preview AS BLOB)) <= 8192), expires_at REAL NOT NULL, created_at REAL NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','approved','denied','expired','stale')), response INTEGER CHECK(response IN (0,1)), consumed_at REAL, kind TEXT NOT NULL CHECK(kind IN ('approval','decision')))",
    "ALTER TABLE approval_requests ADD COLUMN decision_transition TEXT",
    "ALTER TABLE approval_requests ADD COLUMN post_state_version TEXT",
    "CREATE INDEX approval_requests_task_created ON approval_requests(task_id,created_at,request_id)",
)
APPROVAL_COLUMNS = {"approval_requests": {
    "request_id", "task_id", "thread_id", "action_id", "action_digest", "workspace_root",
    "state_version", "owner_instance_id", "preview", "expires_at", "created_at",
    "status", "response", "consumed_at", "kind", "decision_transition", "post_state_version",
}}
