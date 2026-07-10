# User Interfaces
通过同一内核提供全屏 TUI、单次 CLI 和机器可读命令模式。

## 边界
- 负责：交互输入、流式时间线、审批提示、Diff 预览、状态显示、会话选择和 JSON 输出。
- 负责：`agent`、`agent ask`、`agent resume` 与 `agent run --json` 的一致用户语义。
- 不负责：复制 Agent 状态机、直接执行工具、直接访问 provider 或绕过权限决定。
- 不负责：首版 IDE 插件、Web UI、远程多用户服务或桌面应用。

