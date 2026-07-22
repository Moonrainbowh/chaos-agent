# Configured MCP Servers
以 MCP SDK 管理已批准 stdio 服务，并经策略桥暴露有界工具能力。

## 边界
- 负责：读取结构化 stdio 配置、已批准服务器的健康状态、工具 schema、命名空间、风险类别和受限生命周期。
- 负责：通过 SDK 初始化、列出工具、调用、取消、超时、重连和关闭；stdout 仅用于协议，stderr 有界脱敏。
- 负责：提供 Host-neutral 的列表、状态、工具、启用、禁用、重启和诊断 Controller 语义，以及不可变 server/tool generation snapshot。
- 负责：运行中禁用立即阻止新调用，已开始调用按取消协议收敛；启用、重启和工具目录替换只在安全边界发布。
- 不负责：安装服务器、拼接 shell 启动命令、登录或绕过 `ActionPolicy`。
- 依赖：服务器配置由应用集成层提供；只有 enabled 且批准的服务器可启动和暴露 `mcp.<server>.<tool>`。
- Picker、命令和插件引用必须绑定 server/tool generation；旧 generation、未知风险映射和撤销后的调用失败闭合。

## Units
- `McpRegistry`: 列出配置状态并生成受限 server/tool 命名空间 | 维护服务状态 | 未配置、未批准或未映射工具显式拒绝
- `McpSdkAdapter`、`StdioMcpManager`: 隔离 SDK 并管理 stdio 服务生命周期 | 子进程/协议 I/O | 所有超时和取消有界且可关闭
- `McpPolicyBridge`: 将 MCP 调用转换为 typed action request | 调用 ActionPolicy | 未映射风险默认拒绝
- `McpSnapshot`、`McpController.snapshot`、`diagnose`：发布 generation-bound server/tool 目录与脱敏健康状态 | 读取运行状态 | enable 握手成功后发布，disable 先撤下工具再取消关闭；共享风险表只更新 MCP 自己拥有的 namespace
