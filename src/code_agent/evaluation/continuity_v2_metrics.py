"""Trace-based dimensions: lists, failed reads, and blocked edits cannot earn credit."""
import json


def action_metrics(rows, probe):
    denied = [r for r in rows if r.get("policy_rejection")]
    successful = [r for r in rows if not r["result"]["is_error"]]
    history_reads = [r for r in successful if r["request"]["name"] == "history_read_item"]
    notes = [r for r in successful if r["request"]["name"].startswith("notes_")]
    def note_actions(name, stages):
        return [r for r in notes if r["request"]["name"] == name and r.get("stage") in stages
                and r["request"]["arguments"].get("path") == "reader-contract.md"]
    writes_before = note_actions("notes_write_file", (1, 2))
    writes_after = note_actions("notes_write_file", (3, 4))
    reads = note_actions("notes_read_file", (3, 4))
    before = [r["request"]["arguments"].get("text", "").strip() for r in writes_before]
    after = [r["request"]["arguments"].get("text", "").strip() for r in writes_after]
    changed = bool(before and after and before[-1] and after[-1] and before[-1] != after[-1])
    # Reading the new note after overwriting the old one is not stale-note retrieval.
    read_old = any(rows.index(writes_before[-1]) < rows.index(r) < rows.index(writes_after[0])
                   for r in reads) if writes_before and writes_after else False
    lifecycle = bool(changed and read_old)
    return {"constraint_attempts": [{"stage": r.get("stage"),
                                     "tool": r["request"]["name"],
                                     "path": r["request"]["arguments"].get("path"),
                                     "reason": r["policy_rejection"]} for r in denied],
            "constraint_attempt_count": len(denied), "history_content_reads": len(history_reads),
            "note_content_reads": sum(r["request"]["name"] == "notes_read_file" for r in notes),
            "note_lifecycle": "observed" if lifecycle else "not-covered",
            "note_semantics": "review-required" if lifecycle else "not-covered",
            "memory_mode": "explicit-note-probe" if probe else "natural",
            "spontaneous_memory_benefit": "not-established"}


def read_metrics(path, probe):
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    return action_metrics(rows, probe)
