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
    return ImageFont.truetype(str(Path("C:/Windows/Fonts") / name), size)


def ansi_color(code):
    if not code or code == "0":
        return "#eeeeee"
    index = int(code.split(";")[-1])
    if index >= 232:
        return (8 + (index - 232) * 10,) * 3
    levels = (0, 95, 135, 175, 215, 255)
    index -= 16
    return levels[index // 36], levels[index // 6 % 6], levels[index % 6]


def draw_terminal(draw, value, x, y, *, size=17, step=27):
    latin = font("consola.ttf", size)
    chinese = font("msyh.ttc", size - 1)
    symbols = font("seguisym.ttf", size)
    cell = latin.getlength("M")
    column, color, offset = 0, "#eeeeee", 0

    def segment(text):
        nonlocal column, y
        text = CONTROL.sub("", text).replace("\r", "")
        for cluster in graphemes(text):
            if cluster == "\n":
                column, y = 0, y + step
                continue
            face = chinese if display_width(cluster) == 2 else (symbols if ord(cluster[0]) > 255 and cluster not in "─│┌┐└┘╭╮╰╯" else latin)
            draw.text((x + column * cell, y), cluster, fill=color, font=face)
            column += display_width(cluster)

    for match in SGR.finditer(value):
        segment(value[offset:match.start()])
        color = ansi_color(match[1])
        offset = match.end()
    segment(value[offset:])
    return y


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sheet = Image.new("RGB", (1620, 1720), "#101316")
    draw = ImageDraw.Draw(sheet)
    draw.text((65, 40), "CHAOS / TERMINAL COLLECTION", font=font("consolab.ttf", 28), fill="#eeeeeb")
    draw.text((65, 87), "三套界面，同一个专注的工作空间。", font=font("msyh.ttc", 23), fill="#b6bec4")
    notes = (("01", "青蓝冷光 / 圆角边界", "#171c22"),
             ("02", "暖金纸感 / 直角轮廓", "#211e19"),
             ("03", "黑白层次 / 开放线框", "#171717"))
    for index, ((theme, design), (number, subtitle, bg)) in enumerate(zip(DESIGNS.items(), notes)):
        y = 155 + index * 490
        accent = ansi_color(design.accent)
        draw.rounded_rectangle((60, y, 1560, y + 455), radius=14 if index == 0 else 2,
                               fill=bg, outline="#42484d", width=1)
        draw.text((94, y + 22), number + " / " + design.name, font=font("consolab.ttf", 29), fill=accent)
        draw.text((1160, y + 30), subtitle, font=font("msyh.ttc", 19), fill="#b8bec0")
        draw.line((94, y + 71, 1526, y + 71), fill="#3c4145")
        transcript = render_entries(SAMPLE, 95, theme=theme, color=ColorMode.ALWAYS)
        end = draw_terminal(draw, transcript, 95, y + 91)
        frame = preview_frame(theme, width=112)
        draw_terminal(draw, frame.text, 95, end + 41)
        draw.text((1285, y + 132), ":theme " + theme.value, font=font("consola.ttf", 18), fill=accent)
        draw.text((1285, y + 175), "Enter  发送\nCtrl+J  换行\n:  命令面板", font=font("msyh.ttc", 17), fill="#acb1b5", spacing=13)
    draw.text((65, 1650), "真实终端渲染器的静态输出 · 示例对话不代表实际执行 · 背景仅为深色终端参考", font=font("msyh.ttc", 18), fill="#a6adb3")
    sheet.save(OUT / "three-themes.png")
    print(OUT / "three-themes.png")


if __name__ == "__main__":
    main()
