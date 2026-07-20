from __future__ import annotations


REWIND_MIGRATION = (
    "CREATE TABLE workspace_rewind_coverage ("
    "workspace_fingerprint TEXT NOT NULL PRIMARY KEY, "
    "generation INTEGER NOT NULL CHECK(generation > 0), "
    "state TEXT NOT NULL CHECK(state IN ('active','invalidated')), "
    "mutation_high_water INTEGER NOT NULL DEFAULT 0 "
    "CHECK(mutation_high_water >= 0), "
    "mutation_count INTEGER NOT NULL DEFAULT 0 CHECK(mutation_count >= 0), "
    "invalidation_reason TEXT, started_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
    "CHECK ((state = 'active' AND invalidation_reason IS NULL) OR "
    "(state = 'invalidated' AND invalidation_reason IS NOT NULL)))",
    "CREATE TABLE workspace_mutations ("
    "sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
    "mutation_id TEXT NOT NULL UNIQUE, "
    "workspace_fingerprint TEXT NOT NULL "
    "REFERENCES workspace_rewind_coverage(workspace_fingerprint), "
    "coverage_generation INTEGER NOT NULL CHECK(coverage_generation > 0), "
    "owner_thread_id TEXT NOT NULL REFERENCES threads(id), "
    "origin_thread_id TEXT NOT NULL REFERENCES threads(id), "
    "task_id TEXT REFERENCES tasks(id), parent_request_id TEXT, "
    "request_id TEXT NOT NULL, action_name TEXT NOT NULL, "
    "path_count INTEGER NOT NULL CHECK(path_count BETWEEN 0 AND 32), "
    "status TEXT NOT NULL "
    "CHECK(status IN ('prepared','completed','aborted','gap')), "
    "gap_reason TEXT, snapshot_handle TEXT, created_at TEXT NOT NULL, "
    "completed_at TEXT, "
    "UNIQUE(workspace_fingerprint, origin_thread_id, request_id), "
    "CHECK ((status = 'gap' AND path_count = 0 AND gap_reason IS NOT NULL "
    "AND snapshot_handle IS NULL AND completed_at IS NOT NULL) OR "
    "(status = 'prepared' AND path_count > 0 AND gap_reason IS NULL "
    "AND snapshot_handle IS NOT NULL AND completed_at IS NULL) OR "
    "(status IN ('completed','aborted') AND path_count > 0 AND gap_reason IS NULL "
    "AND snapshot_handle IS NOT NULL AND completed_at IS NOT NULL)))",
    "CREATE TABLE workspace_mutation_paths ("
    "mutation_sequence INTEGER NOT NULL "
    "REFERENCES workspace_mutations(sequence) ON DELETE CASCADE, "
    "ordinal INTEGER NOT NULL CHECK(ordinal >= 0), path TEXT NOT NULL, "
    "pre_existed INTEGER NOT NULL CHECK(pre_existed IN (0,1)), "
    "pre_sha256 TEXT, baseline TEXT NOT NULL CHECK(baseline IN "
    "('git-staged','git-unstaged','git-untracked',"
    "'non-git-existing','absent','unknown')), "
    "post_existed INTEGER NOT NULL CHECK(post_existed IN (0,1)), "
    "post_sha256 TEXT, PRIMARY KEY(mutation_sequence, ordinal), "
    "UNIQUE(mutation_sequence, path), "
    "CHECK ((pre_existed = 1 AND pre_sha256 IS NOT NULL) OR "
    "(pre_existed = 0 AND pre_sha256 IS NULL)), "
    "CHECK ((post_existed = 1 AND post_sha256 IS NOT NULL) OR "
    "(post_existed = 0 AND post_sha256 IS NULL)))",
    "CREATE TABLE checkpoint_rewind_facts ("
    "checkpoint_id TEXT NOT NULL PRIMARY KEY "
    "REFERENCES checkpoints(id) ON DELETE CASCADE, "
    "owner_thread_id TEXT NOT NULL REFERENCES threads(id), "
    "workspace_fingerprint TEXT NOT NULL "
    "REFERENCES workspace_rewind_coverage(workspace_fingerprint), "
    "coverage_generation INTEGER NOT NULL CHECK(coverage_generation > 0), "
    "mutation_sequence INTEGER NOT NULL CHECK(mutation_sequence >= 0), "
    "mutation_count INTEGER NOT NULL CHECK(mutation_count >= 0), "
    "coverage_state TEXT NOT NULL "
    "CHECK(coverage_state IN ('active','invalidated')), "
    "created_at TEXT NOT NULL)",
    "CREATE TABLE checkpoint_rewind_expectations ("
    "checkpoint_id TEXT NOT NULL PRIMARY KEY "
    "REFERENCES checkpoints(id) ON DELETE CASCADE, "
    "created_at TEXT NOT NULL)",
    "CREATE INDEX workspace_mutations_workspace_sequence "
    "ON workspace_mutations(workspace_fingerprint, sequence)",
    "CREATE INDEX workspace_mutations_owner_sequence "
    "ON workspace_mutations(owner_thread_id, sequence)",
    "CREATE INDEX workspace_mutation_paths_path_sequence "
    "ON workspace_mutation_paths(path, mutation_sequence)",
    "CREATE INDEX checkpoint_rewind_facts_owner "
    "ON checkpoint_rewind_facts(owner_thread_id, mutation_sequence)",
)

REWIND_REQUIRED_COLUMNS = {
    "workspace_rewind_coverage": {
        "workspace_fingerprint",
        "generation",
        "state",
        "mutation_high_water",
        "mutation_count",
        "invalidation_reason",
        "started_at",
        "updated_at",
    },
    "workspace_mutations": {
        "sequence",
        "mutation_id",
        "workspace_fingerprint",
        "coverage_generation",
        "owner_thread_id",
        "origin_thread_id",
        "task_id",
        "parent_request_id",
        "request_id",
        "action_name",
        "path_count",
        "status",
        "gap_reason",
        "snapshot_handle",
        "created_at",
        "completed_at",
    },
    "workspace_mutation_paths": {
        "mutation_sequence",
        "ordinal",
        "path",
        "pre_existed",
        "pre_sha256",
        "baseline",
        "post_existed",
        "post_sha256",
    },
    "checkpoint_rewind_facts": {
        "checkpoint_id",
        "owner_thread_id",
        "workspace_fingerprint",
        "coverage_generation",
        "mutation_sequence",
        "mutation_count",
        "coverage_state",
        "created_at",
    },
    "checkpoint_rewind_expectations": {
        "checkpoint_id",
        "created_at",
    },
}
