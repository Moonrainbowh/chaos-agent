from __future__ import annotations

import shlex
from dataclasses import dataclass
from dataclasses import replace


@dataclass(frozen=True)
class CommandAction:
    name: str
    aliases: tuple[str, ...]
    description: str
    usage: str = ""
    source: str = "host"


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
    source: str = "host"
    controller: str | None = None
    plugin_id: str | None = None
    digest: str | None = None
    generation: int | None = None

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
    CommandSpec(
        "检查点",
        ("checkpoint",),
        "工作区",
        "列出或创建 Checkpoint",
        "<action>",
        requires=("checkpoints",),
        actions=(
            CommandAction("列表", ("list",), "列出 Checkpoint"),
            CommandAction("创建", ("create",), "创建 Checkpoint", "[label]"),
        ),
    ),
    CommandSpec(
        "回退",
        ("rewind",),
        "工作区",
        "预览并执行 Rewind",
        "[checkpoint-id]",
        requires=("checkpoints",),
    ),
    CommandSpec("模式", ("mode",), "能力", "切换下一任务的 Agent mode", "<mode>", requires=("modes",), actions=(
        CommandAction("low", (), "快速直接"),
        CommandAction("medium", (), "均衡执行"),
        CommandAction("high", (), "深度处理"),
        CommandAction("ultra", (), "复杂任务编排"),
    )),
    CommandSpec("权限", ("permission", "permissions"), "能力", "切换下一任务的访问权限", "<permission>", requires=("permissions",), actions=(
        CommandAction("unrestricted", (), "完全访问，非 critical 动作无需审批"),
        CommandAction("plan", (), "仅允许工作区只读操作"),
        CommandAction("ask", (), "写入和命令逐次审批"),
        CommandAction("auto", (), "普通读写自动执行，命令审批"),
        CommandAction("elevated", (), "工作区外访问逐次审批"),
        CommandAction("full-local", (), "本地文件完全访问，命令审批"),
    )),
    CommandSpec(
        "流程",
        ("flow",),
        "任务",
        "显示可信执行流程",
        "[node-id|失败|证据 <node-id>]",
        requires=("workflows",),
    ),
    CommandSpec(
        "技能",
        ("skill", "skills"),
        "能力",
        "管理当前 thread 的 Skills",
        "<action>",
        requires=("skills",),
        actions=(
            CommandAction("列表", ("list",), "列出 Skills", "[--all|--active|--errors]"),
            CommandAction("信息", ("info",), "显示 Skill 信息", "<skill-id>"),
            CommandAction("启用", ("enable",), "启用 Skill", "<skill-id>"),
            CommandAction("禁用", ("disable",), "禁用 Skill", "<skill-id>"),
            CommandAction("来源", ("source",), "显示 Skill 来源", "<skill-id>"),
            CommandAction("重载", ("reload",), "重新发现 Skills"),
        ),
    ),
    CommandSpec(
        "mcp",
        (),
        "能力",
        "管理已配置 MCP servers",
        "<action>",
        requires=("mcp",),
        actions=(
            CommandAction("list", ("列表",), "列出 MCP servers"),
            CommandAction("status", ("状态",), "显示 MCP 状态", "[server]"),
            CommandAction("tools", ("工具",), "列出 MCP tools", "[server]"),
            CommandAction("enable", ("启用",), "启用 MCP server", "<server>"),
            CommandAction("disable", ("禁用",), "禁用 MCP server", "<server>"),
            CommandAction("restart", ("重启",), "重启 MCP server", "<server>"),
            CommandAction("diagnose", ("诊断",), "诊断 MCP server", "<server>"),
        ),
    ),
    CommandSpec(
        "插件",
        ("plugin", "plugins"),
        "能力",
        "管理声明式 Plugins",
        "<action>",
        requires=("plugins",),
        actions=(
            CommandAction("list", ("列表",), "列出 Plugins"),
            CommandAction("status", ("状态",), "显示 Plugin 状态", "[plugin-id]"),
            CommandAction("enable", ("启用",), "启用 Plugin", "<plugin-id>"),
            CommandAction("disable", ("禁用",), "禁用 Plugin", "<plugin-id>"),
            CommandAction("reload", ("重载",), "重新发现 Plugins"),
        ),
    ),
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

    def with_plugin_commands(self, descriptors: object) -> "CommandRegistry":
        dynamic = []
        for descriptor in descriptors:
            qualified_id = descriptor.qualified_id
            if (
                not isinstance(qualified_id, str)
                or "." not in qualified_id
                or qualified_id.startswith(".")
                or qualified_id.endswith(".")
            ):
                raise ValueError("plugin commands must be namespaced")
            dynamic.append(
                CommandSpec(
                    qualified_id,
                    (),
                    "插件",
                    descriptor.description,
                    "[args...]",
                    requires=("plugins",),
                    source="plugin",
                    controller=descriptor.controller,
                    plugin_id=descriptor.plugin_id,
                    digest=descriptor.digest,
                    generation=descriptor.generation,
                )
            )
        return CommandRegistry(self._specs + tuple(dynamic))

    def with_plugin_modes(self, identifiers: object) -> "CommandRegistry":
        checked = tuple(identifiers)
        if any(
            not isinstance(identifier, str) or "." not in identifier
            for identifier in checked
        ):
            raise ValueError("plugin modes must be namespaced")
        specs = []
        for spec in self._specs:
            if spec.name != "模式":
                specs.append(spec)
                continue
            additions = tuple(
                CommandAction(
                    identifier, (), "插件提供的受限模式", source="plugin"
                )
                for identifier in checked
            )
            specs.append(replace(spec, actions=spec.actions + additions))
        return CommandRegistry(tuple(specs))

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
