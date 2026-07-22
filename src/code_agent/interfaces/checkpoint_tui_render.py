from __future__ import annotations

from typing import Any

from .terminal_display import clip_display, safe_text


def render_rewind_rows(flow: Any, width: int) -> tuple[str, ...]:
    if flow.stage.value in {"previewing", "executing"}:
        target = flow.checkpoint.id if flow.checkpoint is not None else "unknown"
        return (
            clip_display(f"{flow.stage.value} · checkpoint {target}", width),
            clip_display(f"mode {flow.mode or 'unknown'}", width),
        )
    if flow.stage.value != "confirm":
        return flow.picker.rows(width)
    value = flow.preview
    assert value is not None and flow.checkpoint is not None
    headings = (
        f"checkpoint {flow.checkpoint.id}",
        f"mode {value.mode.value}",
        "impact: " + mode_impact(value.mode.value),
        f"restore {value.restore_count} · delete {value.delete_count} · "
        f"{value.total_bytes} bytes",
    )
    footer = (
        ("› " if flow.confirmation_choice == 0 else "  ") + "No",
        ("› " if flow.confirmation_choice == 1 else "  ") + "Yes",
        "Enter select · Esc cancel",
    )
    return bounded_preview_rows(headings, value.paths, footer, width)


def mode_impact(mode: str) -> str:
    return {
        "code": "modifies workspace code",
        "session": "forks a replacement task and supersedes the old task",
        "code_and_session": (
            "modifies workspace code and forks a replacement task; "
            "supersedes the old task"
        ),
    }[mode]


def bounded_preview_rows(
    headings: tuple[str, ...],
    paths: tuple[str, ...],
    footer: tuple[str, ...],
    width: int,
) -> tuple[str, ...]:
    shown = paths[:5]
    suffix = (
        (f"… {len(paths) - len(shown)} more",)
        if len(paths) > len(shown)
        else ()
    )
    fixed = tuple(
        _clip_row(row, width, 16_384)
        for row in (*headings, *suffix, *footer)
    )
    remaining = 16_384 - sum(len(row.encode("utf-8")) for row in fixed)
    path_rows = []
    for path in shown:
        row = _clip_row(path, width, remaining)
        if not row:
            break
        path_rows.append(row)
        remaining -= len(row.encode("utf-8"))
    split = len(headings)
    return (*fixed[:split], *path_rows, *fixed[split:])


def _clip_row(value: str, width: int, byte_limit: int) -> str:
    clipped = clip_display(safe_text(value), width)
    if len(clipped.encode("utf-8")) <= byte_limit:
        return clipped
    return clipped.encode("utf-8")[:byte_limit].decode("utf-8", "ignore")
