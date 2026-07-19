from __future__ import annotations

import shlex
from dataclasses import dataclass


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
    CommandSpec("新建", ("new",), "会话", "新建会话"),
    CommandSpec("会话", ("sessions",), "会话", "列出会话", requires=("sessions",)),
    CommandSpec("恢复", ("restore",), "会话", "恢复会话", "<thread-id>", requires=("history",)),
    CommandSpec("任务", ("tasks",), "任务", "列出任务", requires=("tasks",)),
    CommandSpec("接受", ("accept",), "任务", "接受部分交付", "[task-id]", requires=("tasks",)),
    CommandSpec("差异", ("diff",), "工作区", "显示差异"),
    CommandSpec("证据", ("evidence",), "工作区", "显示验证证据", "[task-id]", requires=("evidence",)),
    CommandSpec("模式", ("mode",), "能力", "切换下一任务的 Agent mode", "<mode>", requires=("modes",), actions=(
        CommandAction("low", (), "快速直接"),
        CommandAction("medium", (), "均衡执行"),
        CommandAction("high", (), "深度处理"),
        CommandAction("ultra", (), "复杂任务编排"),
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

    @staticmethod
    def resolve_action(spec: CommandSpec, name: str) -> CommandAction | None:
        query = name.casefold()
        return next(
            (
                action
                for action in spec.actions
                if query == action.name.casefold()
                or any(query == alias.casefold() for alias in action.aliases)
            ),
            None,
        )

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
