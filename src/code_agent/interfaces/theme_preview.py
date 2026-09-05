"""Offline preview of the production renderers; no model, tools or task writes."""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from pathlib import Path

from .i18n import Language
from .terminal_display import DisplayKind, text_entry
from .terminal_renderer import ColorMode, render_entries
from .terminal_status import status_presentation
from .terminal_tail import clear_live_tail, render_live_tail_frame
from .terminal_theme import DESIGNS, Theme


SAMPLE = (
    text_entry(DisplayKind.USER, "帮我梳理项目结构，并找到最合适的修改位置。"),
    text_entry(DisplayKind.TOOL, "Read files · src/app.py, src/routes.py · 2 files"),
    text_entry(DisplayKind.AGENT, "## 从这里开始\n入口位于 `src/app.py`，路由集中在 `src/routes.py`。\n\n- **先看入口**，确认数据如何进入应用。\n- **再看路由**，把本次改动控制在一个模块内。"),
)
STATES = ("idle", "building_context", "streaming_response", "completed", "paused", "approval")
SGR = re.compile(r"\x1b\[([0-9;]*)m")
CONTROL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def preview_frame(theme, state="idle", *, progress=1.0, previous=None, width=94):
    label, icon, code = status_presentation(state, "", None, Language.ZH_CN, theme, int(progress * 9))
    active = state in {"building_context", "streaming_response"}
    return render_live_tail_frame(
        "", label, width, theme=theme, color=ColorMode.ALWAYS,
        status_icon=icon, status_color=code, previous=previous,
        status_context="gpt-5.6-sol · task 3.0k tokens · 00:12",
        assistant_draft="正在整理入口与路由的关系。**下一步**会列出具体修改位置。" if state == "streaming_response" else "",
        active=active, motion_progress=progress,
        palette=("╭─ ACTION REQUIRED ─────────────────────╮", "│ › Deny once     Allow once            │", "╰──────────────────────────────────────╯") if state == "approval" else (),
    )


def ansi_html(value: str) -> str:
    """Export local SGR colors as escaped spans; discard cursor control codes."""
    result, offset, style = [], 0, ""
    for match in SGR.finditer(value):
        text = CONTROL.sub("", value[offset:match.start()]).replace("\r", "")
        result.append(f'<span style="{style}">{html.escape(text)}</span>')
        codes = match[1].split(";")
        style = "font-weight:700;" if codes[0] == "1" else ""
        if "38" in codes and "5" in codes:
            index = int(codes[-1])
            levels = (0, 95, 135, 175, 215, 255)
            if index >= 232:
                rgb = (8 + (index - 232) * 10,) * 3
            else:
                i = index - 16
                rgb = (levels[i // 36], levels[(i // 6) % 6], levels[i % 6])
            style += f"color:rgb{rgb};"
        offset = match.end()
    result.append(html.escape(CONTROL.sub("", value[offset:]).replace("\r", "")))
    return "".join(result)


def export_preview(path: Path) -> None:
    from .theme_preview_page import PAGE
    data = {}
    for theme in DESIGNS:
        transcript = ansi_html(render_entries(SAMPLE, 92, theme=theme, color=ColorMode.ALWAYS))
        data[theme.value] = {
            "transcript": transcript,
            "frames": {state: [ansi_html(preview_frame(theme, state, progress=t / 8).text)
                                for t in range(9)] for state in STATES},
        }
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PAGE.replace("__PREVIEW_DATA__", payload), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--theme", choices=[item.value for item in DESIGNS], default="aurora")
    parser.add_argument("--html", type=Path)
    parser.add_argument("--animate", action="store_true")
    args = parser.parse_args()
    if args.html:
        export_preview(args.html)
        print(args.html.resolve())
        return
    theme = Theme(args.theme)
    sys.stdout.write("\nCHAOS / APPEARANCE PREVIEW · illustrative data, no model call\n\n")
    sys.stdout.write(render_entries(SAMPLE, 92, theme=theme, color=ColorMode.ALWAYS) + "\n\n")
    geometry = None
    for state in STATES if args.animate else ("idle",):
        for tick in range(9) if args.animate else (8,):
            frame = preview_frame(theme, state, progress=tick / 8, previous=geometry)
            sys.stdout.write(frame.text)
            sys.stdout.flush()
            geometry = frame.geometry
            if args.animate:
                time.sleep(.035)
        if args.animate:
            time.sleep(.6)
    sys.stdout.write(clear_live_tail(geometry) + "Preview finished.\n")


if __name__ == "__main__":
    main()
