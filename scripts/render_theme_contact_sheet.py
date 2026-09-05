"""Draw a static comparison from actual ANSI output, without running a browser."""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from code_agent.interfaces.terminal_display import display_width, graphemes
from code_agent.interfaces.terminal_renderer import ColorMode, render_entries
from code_agent.interfaces.terminal_theme import DESIGNS
from code_agent.interfaces.theme_preview import SAMPLE, preview_frame


OUT = Path(__file__).resolve().parents[1] / "docs" / "ui-preview"
SGR = re.compile(r"\x1b\[([0-9;]*)m")
CONTROL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def font(name, size):
    path = Path("C:/Windows/Fonts") / name
    if not path.is_file() and "cascadia" in name.lower():
        path = Path("C:/Windows/Fonts/consola.ttf")
    return ImageFont.truetype(str(path), size)


def ansi_color(code):
    if not code or code == "0":
        return "#eeeeee"
    parts = code.split(";")
    if "38" in parts and parts[parts.index("38") + 1] == "2":
        return tuple(map(int, parts[-3:]))
    index = int(parts[-1])
    if index >= 232:
        return (8 + (index - 232) * 10,) * 3
    levels = (0, 95, 135, 175, 215, 255)
    index -= 16
    return levels[index // 36], levels[index // 6 % 6], levels[index % 6]


def draw_terminal(draw, value, x, y, *, size=17, step=27):
    latin = font("CascadiaCode.ttf", size)
    chinese = font("msyh.ttc", size - 1)
    symbols = font("seguisym.ttf", size)
    cell = latin.getlength("M")
    column, color, offset = 0, "#eeeeee", 0
    background = None

    def segment(text):
        nonlocal column, y
        text = CONTROL.sub("", text).replace("\r", "")
        for cluster in graphemes(text):
            if cluster == "\n":
                column, y = 0, y + step
                continue
            face = chinese if display_width(cluster) == 2 else (symbols if ord(cluster[0]) > 255 and cluster not in "─│┌┐└┘╭╮╰╯" else latin)
            if background:
                draw.rectangle((x + column * cell, y, x + (column + display_width(cluster)) * cell, y + step), fill=background)
            draw.text((x + column * cell, y), cluster, fill=color, font=face)
            column += display_width(cluster)

    for match in SGR.finditer(value):
        segment(value[offset:match.start()])
        if match[1] == "0":
            color, background = "#eeeeee", None
        elif match[1].startswith("48;2;"):
            background = tuple(map(int, match[1].split(";")[-3:]))
        else:
            color = ansi_color(match[1])
        offset = match.end()
    segment(value[offset:])
    return y


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sheet = Image.new("RGB", (1300, 1460), "#07090e")
    draw = ImageDraw.Draw(sheet)
    draw.text((50, 30), "CHAOS / MUTED SLATE", font=font("consolab.ttf", 28), fill="#7dd3fc")
    draw.text((50, 76), "方案 A 冷萃冰阶 · 唯一默认主题", font=font("msyh.ttc", 23), fill="#cbd5e1")
    theme = next(iter(DESIGNS))
    for index, (state, label) in enumerate((("idle", "READY"), ("streaming_response", "STREAMING"), ("approval", "APPROVAL"))):
        y = 130 + index * 420
        draw.rounded_rectangle((40, y, 1260, y + 395), radius=10, fill="#0a0d14", outline="#223046")
        draw.text((65, y + 16), label, font=font("consolab.ttf", 20), fill="#73849c")
        transcript = render_entries(SAMPLE[:2], 95, theme=theme, color=ColorMode.ALWAYS)
        end = draw_terminal(draw, transcript, 65, y + 55)
        draw_terminal(draw, preview_frame(theme, state, width=112, progress=.5).text, 65, end + 35)
    draw.text((50, 1410), "ANSI 渲染输出示意 · 非终端截图 · 对话和用量均为离线示例", font=font("msyh.ttc", 18), fill="#73849c")
    sheet.save(OUT / "muted-slate.png")
    print(OUT / "muted-slate.png")


if __name__ == "__main__":
    main()
