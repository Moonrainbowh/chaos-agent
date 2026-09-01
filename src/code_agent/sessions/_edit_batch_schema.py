from __future__ import annotations


EDIT_BATCH_MIGRATION = (
    "CREATE TABLE workspace_edit_batches ("
    "mutation_sequence INTEGER NOT NULL PRIMARY KEY "
    "REFERENCES workspace_mutations(sequence) ON DELETE CASCADE, "
    "workspace_fingerprint TEXT NOT NULL "
    "REFERENCES workspace_rewind_coverage(workspace_fingerprint), "
    "plan_id TEXT NOT NULL, plan_digest TEXT NOT NULL, "
    "state TEXT NOT NULL CHECK(state IN "
    "('prepared','applying','rolling_back','rolled_back','completed','conflicted')), "
    "conflict_code TEXT, operation_count INTEGER NOT NULL "
    "CHECK(operation_count BETWEEN 1 AND 32), "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, settled_at TEXT, "
    "UNIQUE(workspace_fingerprint, plan_id), "
    "CHECK ((state = 'conflicted' AND conflict_code IS NOT NULL) OR "
    "(state <> 'conflicted' AND conflict_code IS NULL)), "
    "CHECK ((state IN ('rolled_back','completed','conflicted') "
    "AND settled_at IS NOT NULL) OR "
    "(state IN ('prepared','applying','rolling_back') AND settled_at IS NULL)))",
    "CREATE TABLE workspace_edit_batch_operations ("
    "mutation_sequence INTEGER NOT NULL "
    "REFERENCES workspace_edit_batches(mutation_sequence) ON DELETE CASCADE, "
    "ordinal INTEGER NOT NULL CHECK(ordinal >= 0), "
    "kind TEXT NOT NULL CHECK(kind IN ('write','create','delete','move')), "
    "source_path TEXT, source_pre_existed INTEGER "
    "CHECK(source_pre_existed IN (0,1)), source_pre_sha256 TEXT, "
    "source_pre_size INTEGER CHECK(source_pre_size >= 0), "
    "source_post_existed INTEGER CHECK(source_post_existed IN (0,1)), "
    "source_post_sha256 TEXT, source_post_size INTEGER "
    "CHECK(source_post_size >= 0), target_path TEXT NOT NULL, "
    "target_pre_existed INTEGER NOT NULL CHECK(target_pre_existed IN (0,1)), "
    "target_pre_sha256 TEXT, target_pre_size INTEGER NOT NULL "
    "CHECK(target_pre_size >= 0), target_post_existed INTEGER NOT NULL "
    "CHECK(target_post_existed IN (0,1)), target_post_sha256 TEXT, "
    "target_post_size INTEGER NOT NULL CHECK(target_post_size >= 0), "
    "case_only INTEGER NOT NULL CHECK(case_only IN (0,1)), "
    "progress TEXT NOT NULL CHECK(progress IN ('pending','committed')), "
    "committed_at TEXT, PRIMARY KEY(mutation_sequence, ordinal), "
    "CHECK ((kind = 'move' AND source_path IS NOT NULL "
    "AND source_pre_existed IS NOT NULL AND source_pre_size IS NOT NULL "
    "AND source_post_existed IS NOT NULL AND source_post_size IS NOT NULL) OR "
    "(kind <> 'move' AND source_path IS NULL AND source_pre_existed IS NULL "
    "AND source_pre_sha256 IS NULL AND source_pre_size IS NULL "
    "AND source_post_existed IS NULL AND source_post_sha256 IS NULL "
    "AND source_post_size IS NULL)), "
    "CHECK (case_only = 0 OR kind = 'move'), "
    "CHECK (kind <> 'move' OR (source_pre_existed = 1 "
    "AND source_post_existed = 0 AND target_pre_existed = 0 "
    "AND target_post_existed = 1 "
    "AND source_pre_sha256 = target_post_sha256 "
    "AND source_pre_size = target_post_size)), "
    "CHECK ((source_pre_existed = 1 AND source_pre_sha256 IS NOT NULL "
    "AND source_pre_size >= 0) OR (source_pre_existed = 0 "
    "AND source_pre_sha256 IS NULL AND source_pre_size = 0) OR "
    "(source_pre_existed IS NULL AND source_pre_size IS NULL)), "
    "CHECK ((source_post_existed = 1 AND source_post_sha256 IS NOT NULL "
    "AND source_post_size >= 0) OR (source_post_existed = 0 "
    "AND source_post_sha256 IS NULL AND source_post_size = 0) OR "
    "(source_post_existed IS NULL AND source_post_size IS NULL)), "
    "CHECK ((target_pre_existed = 1 AND target_pre_sha256 IS NOT NULL "
    "AND target_pre_size >= 0) OR (target_pre_existed = 0 "
    "AND target_pre_sha256 IS NULL AND target_pre_size = 0)), "
    "CHECK ((target_post_existed = 1 AND target_post_sha256 IS NOT NULL "
    "AND target_post_size >= 0) OR (target_post_existed = 0 "
    "AND target_post_sha256 IS NULL AND target_post_size = 0)), "
    "CHECK ((progress = 'pending' AND committed_at IS NULL) OR "
    "(progress = 'committed' AND committed_at IS NOT NULL)))",
    "CREATE INDEX workspace_edit_batches_workspace_sequence "
    "ON workspace_edit_batches(workspace_fingerprint, mutation_sequence)",
    "CREATE UNIQUE INDEX workspace_edit_batches_one_unresolved "
    "ON workspace_edit_batches(workspace_fingerprint) WHERE state IN "
    "('prepared','applying','rolling_back','conflicted')",
)


EDIT_BATCH_REQUIRED_COLUMNS = {
    "workspace_edit_batches": {
        "mutation_sequence",
        "workspace_fingerprint",
        "plan_id",
        "plan_digest",
        "state",
        "conflict_code",
        "operation_count",
        "created_at",
        "updated_at",
        "settled_at",
    },
    "workspace_edit_batch_operations": {
        "mutation_sequence",
        "ordinal",
        "kind",
        "source_path",
        "source_pre_existed",
        "source_pre_sha256",
        "source_pre_size",
        "source_post_existed",
        "source_post_sha256",
        "source_post_size",
        "target_path",
        "target_pre_existed",
        "target_pre_sha256",
        "target_pre_size",
        "target_post_existed",
        "target_post_sha256",
        "target_post_size",
        "case_only",
        "progress",
        "committed_at",
    },
}
