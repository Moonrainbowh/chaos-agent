"""Project selection state shared by the startup menu and the TUI overlay."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from .input_buffer import InputBuffer
from .terminal_display import clip_display, display_width, safe_text
from .terminal_layout import TouchRegion
from .terminal_mobile import control_row
from .terminal_tail_geometry import LiveTailFrame, tail_geometry


@dataclass(frozen=True)
class ProjectChoice:
    label: str
    root: Path
    available: bool = True


class ProjectPicker:
    """Select explicit directories; never create/delete the project itself."""

    def __init__(self, store, *, current_root=None, has_draft=False):
        self.store = store
        self.current_root = current_root
        self.has_draft = has_draft
        self.mode = "projects"
        self.directory = None
        self.input = InputBuffer()
        self.choices = ()
        self.selected = 0
        self.error = ""
        self.finished = False
        self.result = None
        self.pending = None
        self.confirm_action = "select"

    async def load(self):
        try:
            if self.mode == "projects":
                entries = await asyncio.to_thread(self.store.entries)
                self.choices = tuple(ProjectChoice(item.name, item.root, item.available) for item in entries)
            elif self.mode == "browse":
                roots = await asyncio.to_thread(self.store.child_directories, self.directory) if self.directory else await asyncio.to_thread(self.store.browse_roots)
                self.choices = tuple(ProjectChoice(root.name or str(root), root) for root in roots)
            self.selected = min(self.selected, max(0, len(self.matches) - 1))
        except (OSError, ValueError, RuntimeError) as error:
            self.error = str(error)
            self.choices = ()

    @property
    def matches(self):
        query = self.input.text.casefold() if self.mode != "path" else ""
        return tuple(item for item in self.choices if query in (item.label + " " + str(item.root)).casefold())

    async def handle_key(self, key):
        if key in {"\x1b", "escape", "\x03"}:
            await self.action("back")
        elif key in {"up", "scroll_up", "page_up"}:
            self.selected = max(0, self.selected - (4 if key == "page_up" else 1))
        elif key in {"down", "scroll_down", "page_down", "\t"}:
            limit = 1 if self.mode == "confirm" else len(self.matches) - 1
            self.selected = min(max(0, limit), self.selected + (4 if key == "page_down" else 1))
        elif key == "\r":
            await self.action("enter")
        elif self.mode != "confirm":
            if key.startswith("\x1b[200~") and key.endswith("\x1b[201~"):
                self.input.insert_paste(key[6:-6])
            elif key in {"\x08", "\x7f"}:
                self.input.backspace()
            elif key == "\x15":
                self.input.clear()
            elif key == "left":
                self.input.move_left()
            elif key == "right":
                self.input.move_right()
            elif key.isprintable():
                self.input.insert(key)
            self.selected = 0

    async def action(self, action):
        self.error = ""
        try:
            await self._action(action)
        except (OSError, ValueError, RuntimeError) as error:
            self.error = str(error)

    async def _action(self, action):
        if action.startswith("item:"):
            self.selected = int(action.split(":", 1)[1])
            action = "enter"
        if action in {"up", "down"}:
            await self.handle_key(action)
        elif action == "back":
            if self.mode == "projects":
                self.finished = True
            elif self.mode == "browse" and self.directory is not None:
                parent = self.directory.parent
                self.directory = None if parent == self.directory else parent
                self.input.clear()
                await self.load()
            else:
                self.mode, self.pending = "projects", None
                self.input.clear()
                await self.load()
        elif action in {"browse", "path"}:
            self.mode, self.directory, self.selected = action, None, 0
            self.input.clear()
            await self.load()
        elif action == "remove" and self.mode == "projects" and self.matches:
            self.pending = self.matches[self.selected].root
            self.confirm_action, self.mode, self.selected = "remove", "confirm", 0
        elif action == "use" and self.mode == "browse" and self.directory is not None:
            await self._choose(self.directory, add=True)
        elif action == "enter":
            if self.mode == "confirm":
                if self.selected == 0:
                    await self._action("back")
                elif self.confirm_action == "remove":
                    await asyncio.to_thread(self.store.remove, self.pending)
                    self.mode, self.pending, self.selected = "projects", None, 0
                    await self.load()
                else:
                    await self._commit(self.pending)
            elif self.mode == "path":
                await self._choose(Path(self.input.text.strip()).expanduser(), add=True)
            elif self.matches:
                choice = self.matches[self.selected]
                if not choice.available:
                    raise ValueError("目录已不可用，请选择其他项目或移除入口")
                if self.mode == "browse":
                    self.directory, self.selected = choice.root, 0
                    self.input.clear()
                    await self.load()
                else:
                    await self._choose(choice.root)

    async def _choose(self, root, *, add=False):
        if add:
            entry = await asyncio.to_thread(self.store.add, root)
            root = entry.root
        if self.has_draft and root != self.current_root:
            self.pending = root
            self.mode, self.confirm_action, self.selected = "confirm", "select", 0
        else:
            await self._commit(root)

    async def _commit(self, root):
        self.result = await asyncio.to_thread(self.store.select, root)
        self.finished = True

    def frame(self, width, height):
        width, height = max(1, width), max(1, height)
        title = {"projects": "项目 · 选择后进入", "browse": "添加项目 · 浏览文件夹", "path": "添加项目 · 输入绝对路径", "confirm": "确认移除入口" if self.confirm_action == "remove" else "确认切换项目"}[self.mode]
        detail = str(self.directory or self.current_root or "点击项目直接进入；最近项目排在前面")
        if self.mode == "confirm":
            detail = "只移除列表记录，不删除工程" if self.confirm_action == "remove" else "切换将放弃未发送的文字和附件；取消则保留"
        lines = [title, detail, ("路径: " if self.mode == "path" else "筛选: ") + self.input.text]
        if self.error:
            lines.append("! " + self.error)
        regions = []
        remaining = max(0, height - len(lines) - 2)
        choices = (ProjectChoice("取消", Path(".")), ProjectChoice("确认", Path("."))) if self.mode == "confirm" else self.matches
        start = min(max(0, self.selected - remaining + 1), max(0, len(choices) - remaining))
        for index in range(start, min(len(choices), start + remaining)):
            item = choices[index]
            label = ("› " if index == self.selected else "  ") + item.label
            if self.mode != "confirm":
                label += " · " + (str(item.root) if item.available else "目录不可用")
            regions.append(TouchRegion("item:" + str(index), len(lines), 0, width))
            lines.append(label)
        while len(lines) < max(0, height - 2):
            lines.append("")
        if self.mode == "projects":
            actions = (("browse", "添加"), ("path", "路径"), ("remove", "移除"), ("back", "返回"))
        elif self.mode == "browse":
            actions = (("use", "选此目录"), ("up", "上移"), ("down", "下移"), ("back", "上级"))
        else:
            actions = (("enter", "选择"), ("up", "上移"), ("down", "下移"), ("back", "取消"))
        if height >= 5:
            lines.append("Enter 选择 · Esc 返回 · 可点击列表")
            control, targets = control_row(actions, width, len(lines))
            lines.append(control)
            regions.extend(targets)
        lines = [clip_display(safe_text(line).replace("\n", " "), width) for line in lines[:height]]
        cursor_row = min(2, len(lines) - 1)
        cursor_column = min(width - 1, display_width(self.input.text) + 6)
        geometry = tail_geometry(lines, cursor_row, cursor_column)
        text = "\x1b[2J\x1b[H" + "\n\r".join(lines)
        text += f"\x1b[{cursor_row + 1};{cursor_column + 1}H"
        return LiveTailFrame(text, geometry, tuple(region for region in regions if region.row < len(lines)))
