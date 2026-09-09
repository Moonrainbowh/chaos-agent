"""Opt-in local diagnostics. Never inserted into prompts or provider usage."""
from __future__ import annotations

import json
from collections.abc import Mapping

from .repo_tiered_context import TierSelection


def selection_report(selection: TierSelection, rendered: str) -> dict[str, object]:
    """Compare bounded pre-budget candidates with the actual rendered records."""
    records = []
    for line in rendered.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and "path" in value:
            records.append(value)
    selected = [record for record in records if "tier" in record]
    for record in selected:
        if record["tier"] == "L0" and record["source"]:
            record["source_range"] = [record["range"][0],
                                      record["range"][0] + record["source"].count("\n") - 1]
    decisions = []
    for tier, node in selection.candidates:
        final = next((record for record in selected
                      if record["path"] == node.path
                      and record["range"] == [node.start_line, node.end_line]
                      and record["symbol"] == node.symbol), None)
        oversized = tier == "L0" and node.end_line - node.start_line + 1 > 400
        provisional = any(node in group for group in (selection.l0, selection.l1, selection.l2))
        status = "removed" if final is None else (
            "kept" if final["tier"] == tier else "downgraded"
        )
        decisions.append({
            "path": node.path, "range": [node.start_line, node.end_line],
            "symbol": node.symbol, "candidate_tier": tier,
            "final_tier": final["tier"] if final else None,
            "status": status, "selection_reasons": list(node.reasons),
            "stage": "final_render" if provisional else "pre_render_packing",
            "reason": "selected" if status == "kept" else (
                "400-line source limit and/or rendered budget" if oversized
                else "rendered token budget" if provisional else "pre-render budget/tier capacity"
            ),
        })
    return {
        "generation": selection.generation,
        "selected": selected,
        "deferred": [r for r in records if "tier" not in r],
        "decisions": decisions,
        "candidate_scope": "bounded L0/L1/L2 candidates before budget packing",
        "compact_fallback": bool(rendered and not records),
    }


def complete_report(
    report: dict[str, object], rendered: str, measurements: Mapping[str, int],
) -> dict[str, object]:
    """Attach exact final repo text and local counts after the prompt budget check."""
    return {
        "selected": [], "deferred": [], "decisions": [],
        **report,
        "rendered_repo_context": rendered,
        "measurements": dict(measurements),
        "token_source": "local estimate; not model API usage",
        "measurement_scope": "WorkspaceContextBuilder result; outer wrappers refresh bundle counters separately",
    }
