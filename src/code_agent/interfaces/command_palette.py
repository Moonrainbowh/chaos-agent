from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PaletteItem:
    alias: str
    label: str
    usage: str = ""

    @property
    def display(self) -> str:
        return "/" + self.label + (" " + self.usage if self.usage else "")


_ITEMS = (
    PaletteItem("help", "帮助"), PaletteItem("status", "状态"),
    PaletteItem("tasks", "任务"), PaletteItem("pause", "暂停", "[id]"),
    PaletteItem("resume", "继续", "[id]"), PaletteItem("stop", "停止", "[id]"),
    PaletteItem("steer", "引导", "<text>"), PaletteItem("diff", "差异"),
    PaletteItem("language", "语言", "zh-CN|en"), PaletteItem("theme", "主题", "signal|symbol|plain"),
    PaletteItem("color", "颜色", "auto|always|never"), PaletteItem("glyphs", "字形", "ascii|unicode"),
    PaletteItem("model", "模型", "列表|使用 <profile>"),
    PaletteItem("skills", "技能", "信息|启用|禁用 <id>"), PaletteItem("mcp", "mcp", "状态 [server]"),
)


def filter_palette(input_text: str, *, limit: int = 6) -> tuple[PaletteItem, ...]:
    if not isinstance(input_text, str) or not input_text.startswith("/"):
        return ()
    query = input_text[1:].strip().casefold()
    return tuple(item for item in _ITEMS if query in item.alias or query in item.label.casefold())[:limit]
