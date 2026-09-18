"""Observe actual intermediate implementations, never infer challenge from final pass."""
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path

from .continuity_driver import _check
from .continuity_v2_fixture import READER_V1, READER_V2, behavior_oracle
from .continuity_v2_plan import ROUNDS, events
from .observation import manifest_digest, workspace_manifest
from .verifier import SubprocessVerifier, run_trusted_verifier


async def observe(workspace, evolved, label, artifact_dir=None):
    """Hidden behavior checks execute only in an independent disposable clone."""
    oracle = behavior_oracle(workspace, evolved)
    before = manifest_digest(workspace_manifest(workspace))
    if artifact_dir is not None:
        shutil.copytree(workspace, artifact_dir / label)
    with tempfile.TemporaryDirectory(prefix="continuity-v2-observe-") as temporary:
        outcome = await run_trusted_verifier(workspace, Path(temporary), oracle, label, SubprocessVerifier())
    if before != manifest_digest(workspace_manifest(workspace)):
        raise ValueError("observation changed active workspace")
    return {"label": label, "workspace_digest": before, **asdict(outcome),
            "passed": outcome.exit_code == 0 and not outcome.infrastructure_failure and not outcome.timed_out}


def coverage(observations):
    by_label = {row["label"]: row for row in observations}
    valid = all(not r["infrastructure_failure"] and not r["timed_out"]
                and r["exit_code"] is not None for r in observations)
    if not valid:
        return {"status": "invalid-infrastructure", "old_solution_invalidated": False,
                "unfinished_across_boundaries": False}
    passed = lambda label: by_label[label]["passed"]
    invalidated = passed("before-patch") and not passed("after-patch")
    pending = not passed("after-diagnosis-1") and not passed("after-diagnosis-3")
    covered = invalidated and pending and passed("final")
    return {"status": "covered" if covered else "not-covered",
            "old_solution_invalidated": invalidated, "unfinished_across_boundaries": pending,
            "final_behavior_passed": passed("final")}


async def drive(workspace, variant, adapter, artifact_dir=None):
    previous, evidence = None, None
    journal, observations = [], []
    stage = 0
    for event in events(variant):
        if event.action == "patch":
            path = workspace / "reader.py"
            if path.is_symlink() or path.read_text(encoding="utf-8") != READER_V1:
                raise ValueError("external patch precondition changed")
            path.write_text(READER_V2, encoding="utf-8")
            evidence = None
            observations.append(await observe(workspace, True, "after-patch", artifact_dir))
        elif event.action in ("work", "switch", "pause", "resume"):
            if event.action == "work":
                stage += 1
                await adapter.set_stage(stage)
                current = await adapter.work(workspace, event.message, ROUNDS[stage - 1])
                evidence = current.verified_digest
                label = {1: "after-diagnosis-1", 2: "before-patch",
                         3: "after-diagnosis-3", 4: "final"}.get(stage)
                if label is not None:
                    observations.append(await observe(workspace, stage >= 3, label, artifact_dir))
            else:
                current = await getattr(adapter, event.action)()
            _check(previous, current, event.action)
            previous = current
        journal.append({"event": event.identifier, "action": event.action,
                        "workspace_digest": manifest_digest(workspace_manifest(workspace))})
    final_digest = manifest_digest(workspace_manifest(workspace))
    return {"events": journal, "observations": observations, "coverage": coverage(observations),
            "fresh_agent_verification": evidence == final_digest,
            "final_digest": final_digest, "variant": variant}
