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
- `LifecycleOwner`：单一 Task 持有 SDK 生命周期及有界调用队列；启动含握手/发现、调用/健康探测、关闭各有期限，取消请求只通知 owner，本 Task 清理；故障不自动重放，须显式重启。健康含 configured/ready/last_success/fault/error_type，只记错误类别，不复制服务 stderr。
- SDK 1.29.1 的 stdio pre-yield 取消可能跳过 shutdown finally；启动 watchdog 只通过本 adapter 的 SDK context 取其进程/stream句柄，用 SDK 自带进程树终止及 stream关闭回收，不枚举 PID、不改协议；取消scope仍由原 owner退出。私有适配点变更须重跑真实 SDK pre-yield 故障测试。
- 关闭 watchdog 同样覆盖 deadline 打断 SDK shutdown finally 的窗口；小于 SDK 默认宽限的合法 close预算仍须回收其所属进程，不能只返回超时后留后台服务。
- `McpPolicyBridge`: 将 MCP 调用转换为 typed action request | 调用 ActionPolicy | 未映射风险默认拒绝
- `McpSnapshot`、`McpController.snapshot`、`diagnose`：发布 generation-bound server/tool 目录与脱敏健康状态 | 读取运行状态 | enable 以候选启用态完成握手后原子发布 `enabled=True`，disable 先发布 `enabled=False` 并撤下工具再取消关闭；共享风险表只更新 MCP 自己拥有的 namespace
- `McpController.call(..., expected_generation=None)`：批准调用可绑定 generation；执行前拒绝旧代际，故障撤下目录/本地风险映射。较晚 disable 使尚未完成 enable 失效，不等待挂起调用；发现 schema 仅与本地 risk 表交集发布。
- `call(..., before_call=None)`：源与代际同步核验传到 owner，在出队后真正 SDK 调用前无 await 执行；排队期间撤销/变更仅拒绝该动作，未触发副作用，不误判健康连接故障。无源的旧直接API兼容。
- `McpBusyError`：有界队列已满，明确在 SDK 调用前拒绝该请求；保留健康连接、目录/风险/代际及先前合法排队请求，不当作传输故障。
