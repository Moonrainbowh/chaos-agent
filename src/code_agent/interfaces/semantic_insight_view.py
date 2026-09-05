from __future__ import annotations

from code_agent.semantic_insights.models import SemanticInsightReport


def format_semantic_insight(report: SemanticInsightReport) -> str:
    """Render one bounded semantic report for terminal and JSON-safe transcripts."""
    if not isinstance(report, SemanticInsightReport):
        raise TypeError("report must be a SemanticInsightReport")
    lines = [f"{report.title} · semantic generation {report.generation}"]
    if report.summary:
        lines.append(_clip(report.summary, 500))
    for section in report.sections:
        lines.extend(("", section.title))
        if section.total > len(section.items):
            end = section.offset + len(section.items)
            start = section.offset + 1 if section.items else section.offset
            lines.append(f"  Showing {start}-{end} of {section.total}; use --offset={end} --limit=50 or narrow scope.")
        if not section.items:
            lines.append("  (end of results)" if section.total else "  (no static matches)")
            continue
        for index, item in enumerate(section.items, section.offset + 1):
            score = f" · score {item.score}" if item.score is not None else ""
            confidence = (
                f" · {item.confidence}"
                if item.confidence not in {"exact", "static"}
                else ""
            )
            lines.append(
                f"  {index}. {_clip(item.label, 180)}{score}{confidence}"
            )
            if item.detail:
                lines.append(f"     {_clip(item.detail, 240)}")
    if report.warnings:
        lines.extend(("", "Limits"))
        lines.extend(f"  - {_clip(value, 300)}" for value in report.warnings)
    return "\n".join(lines)


def _clip(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"
