from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from code_agent.core.limits import (  # noqa: E402
    BudgetLeaseTier,
    BudgetReserveStatus,
    EngineLimits,
    TaskProgressSnapshot,
)
from code_agent.core.task import TaskAuthorization, TaskContract  # noqa: E402
from code_agent.core.task_state import TaskState  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402
from code_agent.sessions.workspace_models import (  # noqa: E402
    CheckpointCursor,
    WorkspaceLineageRecord,
    WorkspaceSnapshotStatus,
)


async def _run() -> dict[str, bool]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        database = root / "sessions.sqlite3"
        sessions = SQLiteSessionRepository(database)
        limits = EngineLimits(max_agent_rounds=50, max_tool_calls=128)
        checks: dict[str, bool] = {}

        quick_thread = await sessions.create_thread()
        quick_task = await sessions.create_task(
            quick_thread,
            TaskContract("inspect", TaskAuthorization.local_workspace(str(root))),
        )
        quick = await sessions.get_or_create_task_budget(
            quick_thread, "selfcheck", limits, BudgetLeaseTier.QUICK
        )
        initial = TaskProgressSnapshot(
            action_fingerprint="read-a", reason="initial read result"
        )
        at_boundary = await sessions.reserve_task_budget(
            quick_thread, model_turns=4, tool_calls=8, progress=initial
        )
        no_progress = await sessions.reserve_task_budget(
            quick_thread, model_turns=1, progress=initial
        )
        progressed = TaskProgressSnapshot(
            action_fingerprint="read-b", reason="new read result"
        )
        renewed = await sessions.reserve_task_budget(
            quick_thread, model_turns=1, progress=progressed
        )
        checks["quick_initial"] = (
            quick.lease_model_turn_limit == 4
            and quick.lease_tool_call_limit == 8
            and at_boundary.status is BudgetReserveStatus.RESERVED
        )
        checks["no_progress_converges"] = (
            no_progress.status is BudgetReserveStatus.LEASE_EXHAUSTED
        )
        checks["trusted_progress_renews"] = (
            renewed.status is BudgetReserveStatus.RENEWED
            and renewed.budget.lease_tier is BudgetLeaseTier.STANDARD
        )

        repeated = await sessions.reserve_task_budget(
            quick_thread, model_turns=7, progress=progressed
        )
        repeated_blocked = await sessions.reserve_task_budget(
            quick_thread, model_turns=1, progress=progressed
        )
        checks["same_progress_does_not_renew_twice"] = (
            repeated.status is BudgetReserveStatus.RESERVED
            and repeated_blocked.status is BudgetReserveStatus.LEASE_EXHAUSTED
        )

        reopened = SQLiteSessionRepository(database)
        restored = await reopened.get_or_create_task_budget(
            quick_thread, "other-model", limits, BudgetLeaseTier.DEEP
        )
        checks["restart_preserves_lease"] = (
            restored == await reopened.load_task_budget(quick_task.id)
            and restored.lease_tier is BudgetLeaseTier.STANDARD
            and restored.lease_renewals == 1
        )

        standard_thread = await reopened.create_thread()
        await reopened.create_task(
            standard_thread,
            TaskContract("repair", TaskAuthorization.local_workspace(str(root))),
        )
        await reopened.get_or_create_task_budget(
            standard_thread, "selfcheck", limits, BudgetLeaseTier.STANDARD
        )
        standard_initial = TaskProgressSnapshot(
            code_generation=1,
            subject_hash="a" * 64,
            reason="initial code generation",
        )
        await reopened.reserve_task_budget(
            standard_thread, model_turns=12, tool_calls=30, progress=standard_initial
        )
        standard_renewed = await reopened.reserve_task_budget(
            standard_thread,
            model_turns=1,
            progress=TaskProgressSnapshot(
                code_generation=2,
                subject_hash="b" * 64,
                reason="new code generation",
            ),
        )
        checks["standard_renews_to_deep"] = (
            standard_renewed.status is BudgetReserveStatus.RENEWED
            and standard_renewed.budget.lease_tier is BudgetLeaseTier.DEEP
            and standard_renewed.budget.lease_model_turn_limit == 30
        )

        deep_thread = await reopened.create_thread()
        deep_task = await reopened.create_task(
            deep_thread,
            TaskContract("deep repair", TaskAuthorization.local_workspace(str(root))),
        )
        await reopened.get_or_create_task_budget(
            deep_thread, "selfcheck", limits, BudgetLeaseTier.DEEP
        )
        deep_initial = TaskProgressSnapshot(
            code_generation=1,
            subject_hash="c" * 64,
            reason="initial code generation",
        )
        await reopened.reserve_task_budget(
            deep_thread, model_turns=30, tool_calls=80, progress=deep_initial
        )
        deep_extended = await reopened.reserve_task_budget(
            deep_thread,
            model_turns=1,
            progress=TaskProgressSnapshot(
                code_generation=2,
                subject_hash="d" * 64,
                reason="new code generation",
            ),
        )
        await reopened.reserve_task_budget(
            deep_thread,
            model_turns=19,
            tool_calls=48,
            progress=TaskProgressSnapshot(
                code_generation=3,
                subject_hash="e" * 64,
                reason="new code generation",
            ),
        )
        hard_blocked = await reopened.reserve_task_budget(
            deep_thread,
            model_turns=1,
            progress=TaskProgressSnapshot(
                code_generation=4,
                subject_hash="f" * 64,
                reason="new code generation",
            ),
        )
        checks["deep_extends_once_to_hard_limit"] = (
            deep_extended.status is BudgetReserveStatus.RENEWED
            and deep_extended.budget.lease_final_extension
            and hard_blocked.status is BudgetReserveStatus.HARD_EXHAUSTED
        )

        lineage = WorkspaceLineageRecord.create(
            repository_id="selfcheck",
            source_root=str(root.resolve()),
            worktree_root=str((root / "managed").resolve()),
            branch_name="selfcheck/deep-lease",
            head_commit="a" * 40,
            owner_task_id=deep_task.id,
        )
        await reopened.create_lineage(lineage)
        deep_budget = await reopened.load_task_budget(deep_task.id)
        cursor = CheckpointCursor.from_records(
            0,
            0,
            (),
            TaskState.empty(),
            deep_budget,
            WorkspaceSnapshotStatus.UNAVAILABLE,
            lineage.id,
        )
        checkpoint = await reopened.publish_workspace_checkpoint(
            deep_thread, "deep lease", {}, None, cursor
        )
        forked = await reopened.fork_task_from_checkpoint(checkpoint)
        fork_budget = await reopened.load_task_budget(forked.id)
        checks["fork_preserves_final_extension"] = (
            fork_budget.lease_final_extension
            and fork_budget.lease_renewals == deep_budget.lease_renewals
            and fork_budget.lease_progress_baseline
            == deep_budget.lease_progress_baseline
            and fork_budget.model_turns == deep_budget.model_turns
            and fork_budget.tool_calls == deep_budget.tool_calls
        )
        return checks


def main() -> int:
    try:
        checks = asyncio.run(_run())
    except Exception as error:
        print(
            json.dumps(
                {
                    "passed": False,
                    "checks": {},
                    "error": type(error).__name__,
                },
                sort_keys=True,
            )
        )
        return 1
    passed = all(checks.values())
    print(json.dumps({"passed": passed, "checks": checks}, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
