from __future__ import annotations

from ._command_models import CommandAction, CommandSpec, CommandVisibility


def _semantic_map_spec() -> CommandSpec:
    return CommandSpec(
        "map", ("repo-map", "semantic-map", "图谱"), "Workspace",
        "Explore the shared semantic repository graph", "[action]",
        requires=("semantic_graph",),
        actions=(
            CommandAction("overview", ("tree",), "Show repository map, subsystems, and dependency hubs", "[prefix]"),
            CommandAction("context", (), "Rank files to show the model for a query", "<query>"),
            CommandAction("impact", (), "Show affected code and tests for paths", "<path...>"),
            CommandAction("tests", (), "Prioritize impacted tests for paths", "<path...>"),
            CommandAction("risk", (), "Predict change risk before editing paths", "<path...>"),
            CommandAction("review", (), "Calculate the bounded review scope", "<path...>"),
            CommandAction("refactor", (), "Plan refactor impact and dependency order", "<path...>"),
            CommandAction("locate", ("bug",), "Rank static bug investigation candidates", "<query>"),
            CommandAction("dead-code", ("dead",), "List conservative static dead-code candidates", "[prefix]"),
        ),
    )


def built_in_command_specs() -> tuple[CommandSpec, ...]:
    return (*_general_specs(), *_workspace_specs(), *_runtime_specs(),
            *_extension_specs(), *_session_specs(), *_advanced_specs())


def _general_specs() -> tuple[CommandSpec, ...]:
    return (
        CommandSpec(
            "clear", ("c", "清屏"), "General",
            "Clear the visible transcript and start a fresh turn",
        ),
        CommandSpec(
            "compact", (), "General",
            "Summarize stale context and persist a semantic checkpoint",
        ),
        CommandSpec(
            "cost", ("tokens",), "General",
            "Show durable prompt/completion usage and configured cost estimate",
        ),
        CommandSpec(
            "status", ("s", "状态"), "General",
            "Show model, task mode, permissions, workspace, and host runtime",
        ),
        CommandSpec(
            "doctor", (), "General",
            "Run PowerShell, Git, path, workspace, and endpoint diagnostics",
        ),
        CommandSpec("exit", ("quit", "q", "退出"), "General", "Exit Chaos-Agent"),
    )


def _workspace_specs() -> tuple[CommandSpec, ...]:
    return (
        CommandSpec("diff", ("d", "差异"), "Workspace", "View uncommitted workspace diff"),
        _semantic_map_spec(),
        CommandSpec(
            "review", (), "Workspace",
            "Ask the agent to review uncommitted changes", "[scope]",
        ),
        CommandSpec(
            "test", ("verify",), "Workspace",
            "Ask the agent to run and report project verification", "[scope]",
        ),
        CommandSpec(
            "rewind", ("undo", "回退"), "Workspace", "Time travel rollback to a checkpoint",
            "[checkpoint-id]", requires=("checkpoints",),
        ),
        CommandSpec(
            "attach", ("attachments", "附件"), "Input", "Attach, view or manage local files/images",
            "[path|clipboard|list|remove <id>|clear]", requires=("attachments",),
        ),
    )


def _runtime_specs() -> tuple[CommandSpec, ...]:
    runtime = ("runtime_selection",)
    return (
        CommandSpec(
            "model", ("m",), "Configuration",
            "Show or switch the configured model profile", "[profile]",
            requires=runtime,
        ),
        _mode_spec(),
        CommandSpec(
            "effort", (), "Configuration",
            "Show or switch reasoning depth", "[low|medium|high|xhigh|max]",
            requires=runtime,
        ),
        _permission_spec(),
    )


def _mode_spec() -> CommandSpec:
    runtime = ("runtime_selection",)
    task_modes = ("task_modes",)
    internal = CommandVisibility.INTERNAL
    return CommandSpec(
            "mode", ("模式",), "Configuration",
            "Choose ask, code, or read-only plan behavior", "<action>",
            actions=(
                CommandAction("ask", (), "Pure Q&A with a read-only task contract", requires=task_modes),
                CommandAction("code", (), "Programming mode with permission-governed tools", requires=task_modes),
                CommandAction("plan", (), "Read-only planning with no writes or local execution", requires=task_modes),
                CommandAction("agent", ("topology", "代理"), "Legacy topology selector", "<single|team>", requires=runtime, visibility=internal),
                CommandAction("model", ("profile", "模型"), "Legacy model selector", "<profile>", requires=runtime, visibility=internal),
                CommandAction("effort", ("reasoning", "思考"), "Legacy effort selector", "<effort>", requires=runtime, visibility=internal),
                CommandAction("low", (), "Legacy low effort", requires=("modes",), visibility=internal),
                CommandAction("medium", (), "Legacy medium effort", requires=("modes",), visibility=internal),
                CommandAction("high", (), "Legacy high effort", requires=("modes",), visibility=internal),
                CommandAction("ultra", (), "Legacy orchestration mode", requires=("modes",), visibility=internal),
            ),
    )


def _permission_spec() -> CommandSpec:
    advanced = CommandVisibility.ADVANCED
    return CommandSpec(
            "permission", ("p", "permissions", "权限"), "Configuration", "Configure tool and sandbox execution permissions",
            "<permission>", requires=("permissions",), actions=(
                CommandAction("auto", (), "Auto-execute workspace reads/writes and commands"),
                CommandAction("plan", (), "Read-only workspace analysis mode"),
                CommandAction("ask", (), "Prompt for approval on every write and command"),
                CommandAction("unrestricted", (), "High trust mode with protected path approval"),
                CommandAction("elevated", (), "Elevated tool access mode", visibility=advanced),
                CommandAction("full-local", (), "Full local workspace trust mode", visibility=advanced),
                CommandAction(
                    "allow-command", ("允许命令",), "Permanently allow a command",
                    "[--network] <program> [args...]", visibility=advanced,
                ),
                CommandAction("rules", ("规则",), "List permanent command permission rules", visibility=advanced),
                CommandAction(
                    "revoke", ("撤销",), "Revoke a permanent command rule", "<rule-id>", visibility=advanced,
                ),
            ),
    )


def _extension_specs() -> tuple[CommandSpec, ...]:
    return (
        CommandSpec(
            "mcp", (), "Extensions", "Inspect and manage Model Context Protocol servers", "<action>",
            requires=("mcp",), actions=(
                CommandAction("list", ("列表",), "List MCP servers"),
                CommandAction("status", ("状态",), "Show MCP server status", "[server]"),
                CommandAction("tools", ("工具",), "List MCP tools", "[server]"),
                CommandAction("enable", ("启用",), "Enable MCP server", "<server>"),
                CommandAction("disable", ("禁用",), "Disable MCP server", "<server>"),
                CommandAction("restart", ("重启",), "Restart MCP server", "<server>"),
                CommandAction("diagnose", ("诊断",), "Diagnose MCP server", "<server>"),
            ),
        ),
        CommandSpec(
            "plugin", ("plugins", "插件"), "Extensions", "Manage local and declarative plugins", "<action>",
            requires=("plugins",), actions=(
                CommandAction("list", ("列表",), "List installed plugins"),
                CommandAction("status", ("状态",), "Show plugin status", "[plugin-id]"),
                CommandAction("enable", ("启用",), "Enable plugin", "<plugin-id>"),
                CommandAction("disable", ("禁用",), "Disable plugin", "<plugin-id>"),
                CommandAction("reload", ("重载",), "Reload plugins"),
            ),
        ),
        CommandSpec("tasks", ("t", "任务"), "Tasks", "List background and active tasks", requires=("tasks",)),
    )


def _session_specs() -> tuple[CommandSpec, ...]:
    advanced = CommandVisibility.ADVANCED
    peers = ("peers",)
    return (
        CommandSpec(
            "help", ("帮助", "?"), "General", "Show available commands",
            "[command|all]", visibility=advanced,
        ),
        CommandSpec(
            "sessions", ("会话",), "Session", "Manage session threads and peer agents", "<action>",
            actions=(
                CommandAction("history", ("list", "历史"), "List session history", requires=("sessions",)),
                CommandAction("online", ("agents", "在线"), "List online agents", requires=peers),
                CommandAction("rename", ("重命名",), "Rename current agent", "<name>", requires=peers),
                CommandAction("send", ("发送",), "Send text message to an agent", "<target> <text...>", requires=peers),
                CommandAction("inbound", ("接收",), "Configure inbound policy", "<auto|accept|hold|refuse>", requires=peers),
                CommandAction("inbox", ("pending", "待处理"), "List pending messages", requires=peers),
                CommandAction("accept", ("接受",), "Accept held message", "<message-id>", requires=peers),
                CommandAction("refuse", ("reject", "拒绝"), "Refuse held message", "<message-id>", requires=peers),
            ), visibility=advanced,
        ),
        CommandSpec("new", ("新建",), "Session", "Start a new session", visibility=advanced),
        CommandSpec(
            "restore", ("恢复",), "Session", "Restore session from thread ID", "<thread-id>",
            requires=("history",), visibility=advanced,
        ),
    )


def _advanced_specs() -> tuple[CommandSpec, ...]:
    advanced = CommandVisibility.ADVANCED
    return (
        CommandSpec(
            "accept", ("接受",), "Tasks", "Accept partial delivery", "[task-id]",
            requires=("tasks",), visibility=advanced,
        ),
        CommandSpec(
            "evidence", ("证据",), "Workspace", "Show verification evidence", "[task-id]",
            requires=("evidence",), visibility=advanced,
        ),
        CommandSpec(
            "checkpoint", ("检查点",), "Workspace", "List or create checkpoints", "<action>",
            requires=("checkpoints",), actions=(
                CommandAction("list", ("列表",), "List checkpoints"),
                CommandAction("create", ("创建",), "Create checkpoint", "[label]"),
            ), visibility=advanced,
        ),
        CommandSpec(
            "flow", ("流程",), "Tasks", "Show execution flow graph",
            "[node-id|failure|evidence <node-id>]", requires=("workflows",), visibility=advanced,
        ),
        CommandSpec(
            "skill", ("skills", "技能"), "Capability", "Manage skills for current thread", "<action>",
            requires=("skills",), actions=(
                CommandAction("list", ("列表",), "List skills", "[--all|--active|--errors]"),
                CommandAction("info", ("信息",), "Show skill details", "<skill-id>"),
                CommandAction("enable", ("启用",), "Enable skill", "<skill-id>"),
                CommandAction("disable", ("禁用",), "Disable skill", "<skill-id>"),
                CommandAction("source", ("来源",), "Show skill source", "<skill-id>"),
                CommandAction("reload", ("重载",), "Reload skills"),
            ), visibility=advanced,
        ),
    )
