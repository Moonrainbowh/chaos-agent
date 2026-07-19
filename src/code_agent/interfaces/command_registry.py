from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class CommandAction:
    name: str
    aliases: tuple[str, ...]
    description: str
    usage: str = ""


@dataclass(frozen=True)
class CommandSpec:
    name: str
    aliases: tuple[str, ...]
    group: str
    description: str
    usage: str = ""
    requires: tuple[str, ...] = ()
    accepts_active_task: bool = True
    actions: tuple[CommandAction, ...] = ()

    @property
    def display(self) -> str:
        return "/" + self.name + (" " + self.usage if self.usage else "")


_SPECS = (
    CommandSpec("帮助", ("help",), "通用", "显示可用命令", "[command]"),
    CommandSpec("状态", ("status",), "通用", "显示当前状态"),
    CommandSpec("清屏", ("clear",), "通用", "清空本次转录"),
    CommandSpec("退出", ("exit", "quit"), "通用", "请求退出"),
    CommandSpec("诊断", ("doctor",), "通用", "显示本地诊断"),
    CommandSpec("追踪", ("trace",), "通用", "显示当前追踪"),
    CommandSpec("新建", ("new",), "会话", "新建会话"),
    CommandSpec("会话", ("sessions",), "会话", "列出会话", requires=("sessions",)),
    CommandSpec("打开", ("open",), "会话", "打开会话", "<thread-id>", requires=("history",)),
    CommandSpec("恢复", ("restore",), "会话", "恢复会话", "<thread-id>", requires=("history",)),
    CommandSpec("任务", ("tasks",), "任务", "列出任务", requires=("tasks",)),
    CommandSpec("暂停", ("pause",), "任务", "暂停任务", "[task-id]", requires=("tasks",)),
    CommandSpec("继续", ("resume",), "任务", "继续任务", "[task-id]", requires=("tasks",)),
    CommandSpec("停止", ("stop",), "任务", "停止任务", "[task-id]", requires=("tasks",)),
    CommandSpec("接受", ("accept",), "任务", "接受部分交付", "[task-id]", requires=("tasks",)),
    CommandSpec("引导", ("steer",), "任务", "引导当前任务", "<text>", requires=("tasks",)),
    CommandSpec("差异", ("diff",), "工作区", "显示差异"),
    CommandSpec("上下文", ("context",), "工作区", "显示上下文预算"),
    CommandSpec("工具", ("tools",), "工作区", "显示可用工具"),
    CommandSpec("证据", ("evidence",), "工作区", "显示验证证据", "[task-id]", requires=("evidence",)),
    CommandSpec("回溯", ("rewind",), "工作区", "只读预览 checkpoint 回溯", "<action>", requires=("rewind",), actions=(
        CommandAction("列表", ("list",), "列出 checkpoint 候选", "[cursor]"),
        CommandAction("预览", ("preview",), "预览 checkpoint 回溯", "<checkpoint-id> <conversation|code|both>"),
    )),
    CommandSpec("模型", ("model",), "能力", "管理模型 profile", "<action>", requires=("profiles",), actions=(
        CommandAction("列表", ("list",), "列出已配置模型"),
        CommandAction("使用", ("use",), "选择下一任务模型", "<profile>"),
    )),
    CommandSpec("技能", ("skills",), "能力", "管理 Skills", "<action>", requires=("skills",), actions=(
        CommandAction("列表", ("list",), "列出可用 Skills"),
        CommandAction("信息", ("info",), "查看 Skill 信息", "<id>"),
        CommandAction("启用", ("enable",), "启用 Skill", "<id>"),
        CommandAction("禁用", ("disable",), "禁用 Skill", "<id>"),
    )),
    CommandSpec("mcp", (), "能力", "管理 MCP 服务", "<action>", requires=("mcp",), actions=(
        CommandAction("列表", ("list",), "列出 MCP 服务"),
        CommandAction("状态", ("status",), "查看服务状态", "[server]"),
        CommandAction("启用", ("enable",), "启用服务", "<server>"),
        CommandAction("禁用", ("disable",), "禁用服务", "<server>"),
        CommandAction("重启", ("restart",), "重启服务", "<server>"),
        CommandAction("诊断", ("diagnose",), "查看服务诊断", "[server]"),
    )),
    CommandSpec("语言", ("language",), "显示", "切换语言", "<language>", actions=(
        CommandAction("zh-CN", ("zh",), "中文"), CommandAction("en", ("en-US",), "English"),
    )),
    CommandSpec("主题", ("theme",), "显示", "切换主题", "<theme>", actions=(
        CommandAction("signal", (), "信号主题"), CommandAction("symbol", (), "符号主题"), CommandAction("plain", (), "纯文本主题"),
    )),
    CommandSpec("颜色", ("color",), "显示", "切换颜色", "<mode>", actions=(
        CommandAction("auto", (), "自动颜色"), CommandAction("always", (), "始终启用颜色"), CommandAction("never", (), "禁用颜色"),
    )),
    CommandSpec("字形", ("glyphs",), "显示", "切换字形", "<mode>", actions=(
        CommandAction("unicode", (), "Unicode 字形"), CommandAction("ascii", (), "ASCII 字形"),
    )),
)


class CommandRegistry:
    def __init__(self, specs: tuple[CommandSpec, ...] = _SPECS) -> None:
        self._specs = specs
        self._lookup = {alias.casefold(): spec for spec in specs for alias in (spec.name, *spec.aliases)}
        if len(self._lookup) != sum(1 + len(item.aliases) for item in specs):
            raise ValueError("command aliases must be unique")

    def available(self, services: set[str] | None = None) -> tuple[CommandSpec, ...]:
        services = services or set()
        return tuple(item for item in self._specs if set(item.requires).issubset(services))

    def all(self) -> tuple[CommandSpec, ...]:
        return self._specs

    def resolve(self, name: str) -> CommandSpec | None:
        return self._lookup.get(name.casefold())

    def parse(self, text: str, services: set[str] | None = None) -> tuple[CommandSpec | None, tuple[str, ...], str | None]:
        if not isinstance(text, str):
            raise TypeError("command text must be a string")
        if not text.startswith("/"):
            return None, (), None
        try:
            parts = tuple(shlex.split(text[1:]))
        except ValueError:
            return None, (), "invalid quoted command"
        if not parts:
            return None, (), "slash command is required"
        spec = self._lookup.get(parts[0].casefold())
        if spec is None or spec not in self.available(services):
            return None, (), "unknown or unavailable slash command"
        return spec, parts[1:], None

    def filter(self, text: str, services: set[str] | None = None, limit: int = 6) -> tuple[CommandSpec, ...]:
        if not isinstance(text, str) or not text.startswith("/"):
            return ()
        query = text[1:].strip().casefold()
        return tuple(item for item in self.available(services) if query in item.name.casefold() or any(query in alias.casefold() for alias in item.aliases))[:limit]


REGISTRY = CommandRegistry()
