"""Comparable intervention plans, independent of any model or provider."""
from dataclasses import asdict, dataclass

from .continuity_fixture import VERSION


@dataclass(frozen=True)
class Event:
    identifier: str
    action: str
    message: str = ""


def events(variant: str) -> tuple[Event, ...]:
    """Three fixed work boundaries; no score-dependent intervention timing."""
    if variant not in ("A", "B", "C", "D"):
        raise ValueError("variant must be A, B, C or D")
    result = [Event("work-1", "work", "Read TASK.md, investigate and begin the repair.")]
    for stage in (1, 2):
        result.append(Event(f"boundary-{stage}", "switch" if variant != "A" else "barrier"))
        if stage == 1:
            result.append(Event("work-2", "work", "Continue the repair and tests."))
    if variant == "D":
        result.append(Event("pause", "pause"))
    if variant in ("C", "D"):
        result.append(Event("reader-v2", "patch"))
    if variant == "D":
        result.append(Event("resume", "resume"))
    message = "Continue the original task and check remaining work."
    if variant in ("C", "D"):
        message = "The reader was updated between work stages. Continue the original task."
    result.extend((Event("work-3", "work", message),
                   Event("boundary-3", "switch" if variant != "A" else "barrier"),
                   Event("work-4", "work", "Finish the task and validate the current code."),
                   Event("final", "verify")))
    return tuple(result)


def manifest(variant: str) -> dict:
    """Work slices count completed model/tool rounds, not elapsed wall time."""
    return {"fixture_version": VERSION, "variant": variant,
            "mode": "controlled-boundary", "work_slice_rounds": [4, 4, 4, 8],
            "events": [asdict(event) for event in events(variant)],
            "early_completion": "hold at barrier; deliver later events; never invent turns",
            "switch_receipt": "require committed window id change after complete tool group",
            "pause_receipt": "persist same task; confirm process exit before patch",
            "resume_receipt": "new process instance, same task id and persistent store",
            "memory_policy": "existing History/Notes only; no authored memory injected",
            "real_api_status": "NOT_RUN"}
