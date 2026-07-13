# Configured MCP Servers
以配置审计和策略映射表达 MCP 服务器状态，而不直接扩大 Agent 权限。

## 边界
- 负责：读取已配置服务器的安全摘要、启用状态、工具命名空间和风险类别映射。
- 不负责：安装服务器、执行任意命令、登录、网络连接或绕过 `ActionPolicy`。
- 依赖：服务器配置必须由应用集成层提供；未形成 policy bridge 前仅支持 list/status。

## Units
- `McpRegistry`: 列出配置状态并生成受限 server/tool 命名空间 | 无副作用 | 未配置工具显式拒绝
