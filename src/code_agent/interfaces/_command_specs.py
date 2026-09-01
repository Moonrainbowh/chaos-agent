from __future__ import annotations

from ._command_models import CommandAction, CommandSpec, CommandVisibility


def built_in_command_specs() -> tuple[CommandSpec, ...]:
    advanced = CommandVisibility.ADVANCED
    internal = CommandVisibility.INTERNAL
    runtime = ("runtime_selection",)
    peers = ("peers",)
    return (
        CommandSpec("帮助", ("help",), "通用", "显示常用命令", "[command|全部]"),
        CommandSpec("状态", ("status",), "通用", "显示当前状态"),
        CommandSpec("新建", ("new",), "会话", "新建会话"),
        CommandSpec(
            "会话", ("sessions",), "会话", "管理历史与在线会话", "<action>",
            actions=(
                CommandAction("历史", ("history", "list"), "列出历史会话", requires=("sessions",)),
                CommandAction("在线", ("online", "agents"), "列出在线 Agent", requires=peers),
                CommandAction("重命名", ("rename",), "重命名当前 Agent", "<name>", requires=peers),
                CommandAction("发送", ("send",), "向目标 Agent 发送纯文本", "<target> <text...>", requires=peers),
                CommandAction("接收", ("inbound",), "设置入站策略", "<auto|accept|hold|refuse>", requires=peers),
                CommandAction("待处理", ("inbox", "pending"), "列出待处理消息", requires=peers),
                CommandAction("接受", ("accept",), "接受 held 消息", "<message-id>", requires=peers),
                CommandAction("拒绝", ("refuse", "reject"), "拒绝 held 消息", "<message-id>", requires=peers),
            ),
        ),
        CommandSpec("任务", ("tasks",), "任务", "列出任务", requires=("tasks",)),
        CommandSpec("差异", ("diff",), "工作区", "显示差异"),
        CommandSpec(
            "附件", ("attach", "attachments"), "输入", "暂存、查看或移除附件",
            "[path|clipboard|list|remove <id>|clear]", requires=("attachments",),
        ),
        CommandSpec(
            "回退", ("rewind",), "工作区", "预览并执行 Rewind",
            "[checkpoint-id]", requires=("checkpoints",),
        ),
        CommandSpec(
            "模式", ("mode",), "能力", "选择代理、模型与思考深度", "<action>",
            actions=(
                CommandAction("代理", ("topology", "agent"), "选择 single 或 team", "<single|team>", requires=runtime),
                CommandAction("模型", ("model", "profile"), "选择模型 profile", "<profile-or-sol|terra|luna>", requires=runtime),
                CommandAction("思考", ("reasoning", "effort"), "选择思考深度", "<low|medium|high|xhigh|max>", requires=runtime),
                CommandAction("low", (), "旧版快速模式", requires=("modes",), visibility=internal),
                CommandAction("medium", (), "旧版均衡模式", requires=("modes",), visibility=internal),
                CommandAction("high", (), "旧版深度模式", requires=("modes",), visibility=internal),
                CommandAction("ultra", (), "旧版编排模式", requires=("modes",), visibility=internal),
            ),
        ),
        CommandSpec(
            "权限", ("permission", "permissions"), "能力", "切换下一任务的访问权限",
            "<permission>", requires=("permissions",), actions=(
                CommandAction("unrestricted", (), "高信任访问，受保护路径仍需审批"),
                CommandAction("plan", (), "仅允许工作区只读操作"),
                CommandAction("ask", (), "写入和命令逐次审批"),
                CommandAction("auto", (), "普通读写自动执行，命令审批"),
                CommandAction("elevated", (), "外部访问走审批，类型化文件仍限工作区"),
                CommandAction("full-local", (), "本地高信任策略，类型化文件仍限工作区"),
            ),
        ),
        CommandSpec("退出", ("exit", "quit"), "通用", "请求退出"),
        CommandSpec("清屏", ("clear",), "通用", "清空本次转录", visibility=advanced),
        CommandSpec(
            "恢复", ("restore",), "会话", "恢复会话", "<thread-id>",
            requires=("history",), visibility=advanced,
        ),
        CommandSpec(
            "接受", ("accept",), "任务", "接受部分交付", "[task-id]",
            requires=("tasks",), visibility=advanced,
        ),
        CommandSpec(
            "证据", ("evidence",), "工作区", "显示验证证据", "[task-id]",
            requires=("evidence",), visibility=advanced,
        ),
        CommandSpec(
            "检查点", ("checkpoint",), "工作区", "列出或创建 Checkpoint", "<action>",
            requires=("checkpoints",), actions=(
                CommandAction("列表", ("list",), "列出 Checkpoint"),
                CommandAction("创建", ("create",), "创建 Checkpoint", "[label]"),
            ), visibility=advanced,
        ),
        CommandSpec(
            "流程", ("flow",), "任务", "显示可信执行流程",
            "[node-id|失败|证据 <node-id>]", requires=("workflows",), visibility=advanced,
        ),
        CommandSpec(
            "技能", ("skill", "skills"), "能力", "管理当前 thread 的 Skills", "<action>",
            requires=("skills",), actions=(
                CommandAction("列表", ("list",), "列出 Skills", "[--all|--active|--errors]"),
                CommandAction("信息", ("info",), "显示 Skill 信息", "<skill-id>"),
                CommandAction("启用", ("enable",), "启用 Skill", "<skill-id>"),
                CommandAction("禁用", ("disable",), "禁用 Skill", "<skill-id>"),
                CommandAction("来源", ("source",), "显示 Skill 来源", "<skill-id>"),
                CommandAction("重载", ("reload",), "重新发现 Skills"),
            ), visibility=advanced,
        ),
        CommandSpec(
            "mcp", (), "能力", "管理已配置 MCP servers", "<action>",
            requires=("mcp",), actions=(
                CommandAction("list", ("列表",), "列出 MCP servers"),
                CommandAction("status", ("状态",), "显示 MCP 状态", "[server]"),
                CommandAction("tools", ("工具",), "列出 MCP tools", "[server]"),
                CommandAction("enable", ("启用",), "启用 MCP server", "<server>"),
                CommandAction("disable", ("禁用",), "禁用 MCP server", "<server>"),
                CommandAction("restart", ("重启",), "重启 MCP server", "<server>"),
                CommandAction("diagnose", ("诊断",), "诊断 MCP server", "<server>"),
            ), visibility=advanced,
        ),
        CommandSpec(
            "插件", ("plugin", "plugins"), "能力", "管理声明式 Plugins", "<action>",
            requires=("plugins",), actions=(
                CommandAction("list", ("列表",), "列出 Plugins"),
                CommandAction("status", ("状态",), "显示 Plugin 状态", "[plugin-id]"),
                CommandAction("enable", ("启用",), "启用 Plugin", "<plugin-id>"),
                CommandAction("disable", ("禁用",), "禁用 Plugin", "<plugin-id>"),
                CommandAction("reload", ("重载",), "重新发现 Plugins"),
            ), visibility=advanced,
        ),
    )
