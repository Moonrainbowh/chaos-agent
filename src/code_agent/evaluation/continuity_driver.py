"""Host-side event driver. Adapter receipts must come from production host state."""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .continuity_fixture import READER_V1, READER_V2
from .continuity_plan import events
from .observation import manifest_digest, workspace_manifest


@dataclass(frozen=True)
class Receipt:
    task_id: str
    process_instance: str
    window_id: str
    store_id: str
    tools_settled: bool = True
    process_exited: bool = False
    verified_digest: str | None = None


class ContinuityAdapter(Protocol):
    """Trusted host adapter; never decode these fields from model-written JSON.

    work completes at most `rounds` model/tool rounds and yields at a settled boundary.
    switch commits a real new window; pause persists and awaits process exit;
    resume launches a fresh process against the same task and persistent store.
    verified_digest is set only after an actual successful public verifier run.
    All calls have a runner-level timeout; cancellation must terminate owned processes.
    """
    async def work(self, workspace: Path, message: str, rounds: int) -> Receipt: ...
    async def switch(self) -> Receipt: ...
    async def pause(self) -> Receipt: ...
    async def resume(self) -> Receipt: ...


def _check(previous: Receipt | None, current: Receipt, action: str) -> None:
    if not all((current.task_id, current.process_instance, current.window_id, current.store_id)):
        raise ValueError("missing host identity")
    if not current.tools_settled:
        raise ValueError("intervention inside incomplete tool group")
    if action != "pause" and current.process_exited:
        raise ValueError("active operation returned an exited process")
    if previous is None:
        return
    if (current.task_id, current.store_id) != (previous.task_id, previous.store_id):
        raise ValueError("task or persistent store changed")
    if action == "resume":
        if not previous.process_exited or current.process_instance == previous.process_instance:
            raise ValueError("resume lacks confirmed process restart")
    elif current.process_instance != previous.process_instance:
        raise ValueError("unexpected process restart")
    if action == "switch" and current.window_id == previous.window_id:
        raise ValueError("window switch not committed")
    if action == "pause" and not current.process_exited:
        raise ValueError("pause lacks confirmed exit")


async def drive(workspace: Path, variant: str, adapter: ContinuityAdapter) -> dict:
    """Execute the frozen plan; final hidden verification is the outer runner's job."""
    previous = None
    journal = []
    slices = iter((4, 4, 4, 8))
    evidence = None
    for event in events(variant):
        action = event.action
        if action == "patch":
            path = workspace / "reader.py"
            if path.is_symlink() or path.read_text(encoding="utf-8") != READER_V1:
                raise ValueError("external patch precondition changed")
            path.write_text(READER_V2, encoding="utf-8")
            evidence = None
        elif action in ("work", "switch", "pause", "resume"):
            current = (await adapter.work(workspace, event.message, next(slices))
                       if action == "work" else await getattr(adapter, action)())
            _check(previous, current, action)
            if action == "work" and current.verified_digest is not None:
                evidence = current.verified_digest
            previous = current
        journal.append({"event": event.identifier, "action": action,
                        "workspace_digest": manifest_digest(workspace_manifest(workspace))})
    final_digest = manifest_digest(workspace_manifest(workspace))
    return {"events": journal, "fresh_agent_verification": evidence == final_digest,
            "final_digest": final_digest, "variant": variant}
