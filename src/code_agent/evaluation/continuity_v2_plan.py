"""Predeclared stages and comparisons, independent of model performance."""
from dataclasses import asdict

from .continuity_plan import Event
from .continuity_v2_fixture import VERSION

ROUNDS = (3, 7, 3, 7)


def events(variant):
    if variant not in ("A", "B", "C", "D"):
        raise ValueError("unknown variant")
    probe = variant in ("C", "D")
    boundary = "barrier" if variant == "A" else "switch"
    first = ("Diagnosis stage: read TASK.md and investigate the resume defect. "
             "Do not edit workspace files yet. Identify the repair and remaining checks.")
    repair = "Repair stage: implement the diagnosed repair and verify the installed code."
    if probe:
        repair += (" Before ending, write a nonempty Notes file reader-contract.md describing "
                   "the observed reader interface, the evidence/version inspected, and pending work.")
    result = [Event("work-1", "work", first), Event("boundary-1", boundary),
              Event("work-2", "work", repair), Event("boundary-2", boundary)]
    if variant == "D":
        result.append(Event("pause", "pause"))
    result.append(Event("reader-v2", "patch"))
    if variant == "D":
        result.append(Event("resume", "resume"))
    message = ("Diagnosis stage: the maintainer migrated reader.py. Inspect the current code "
               "and check whether your earlier repair and evidence still apply. "
               "Do not edit workspace files yet; leave the needed repair for the next stage.")
    if probe:
        message += " Read your earlier Notes file reader-contract.md and compare it with current evidence."
    final = "Repair stage: finish the outstanding migration repair and verify the current code."
    if probe:
        final += (" Update Notes file reader-contract.md to correct outdated conclusions, "
                  "identify the current reader interface and record remaining work and validation.")
    return tuple(result + [Event("work-3", "work", message), Event("boundary-3", boundary),
                           Event("work-4", "work", final)])


def manifest(variant):
    return {"fixture_version": VERSION, "variant": variant, "work_slice_rounds": list(ROUNDS),
            "mode": "controlled-staged-migration", "events": [asdict(e) for e in events(variant)],
            "memory_mode": "explicit-note-probe" if variant in "CD" else "natural",
            "comparisons": {"A/B": "same migration; no windows versus three windows",
                            "B/C": "natural memory versus explicit note maintenance",
                            "C/D": "same explicit note probe; with/without process restart"},
            "boundaries": "complete tool groups; fixed stages, never move based on scores",
            "coverage": "prepatch pass + immediate postpatch fail + final pass required",
            "note_semantics": "manual evidence review required; lifecycle is not semantic correctness",
            "real_api_status": "NOT_RUN"}
