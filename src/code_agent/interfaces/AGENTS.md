# User Interfaces
通过同一内核提供全屏 TUI、单次 CLI 和机器可读命令模式。

## 边界
- 负责：交互输入、流式时间线、审批提示、Diff 预览、状态显示、会话选择和 JSON 输出。
- 负责：`agent`、`agent ask`、`agent resume` 与 `agent run --json` 的一致用户语义。
- 负责：以 Windows Terminal 和 PowerShell 中的键盘输入、Unicode、颜色及窗口调整为首版交互验收基线。
- 不负责：复制 Agent 状态机、直接执行工具、直接访问 provider 或绕过权限决定。
- 不负责：首版 Linux/macOS 端到端适配、IDE 插件、Web UI、远程多用户服务或桌面应用。

## Units
- `AgentController.ask`、`resume`、`run_json`: 将同一 `AgentEngine` 事件流提供给交互和非交互调用方 | 调用内核 | 不重解释动作或绕过取消令牌
- `ForegroundTaskController`: 创建、附着、暂停、恢复、停止前台任务，并在安全边界提交 steering | 调用 core runner | 不持有 SQLite 或直接执行工具
- `parse_tui_command(text)`: 解析 TUI 内部 `/任务`、`/暂停`、`/继续`、`/停止`、`/引导`、`/语言` | 无副作用 | 不与进程级 CLI grammar 循环依赖
- `parse_command(arguments): Command`: 解析 `agent`、`ask`、`resume` 和 `run --json` 的稳定命令语义 | 无副作用 | 不打印或直接退出，便于集成入口处理用法错误
- `execute_command(command, controller, tui, write): int`: 把解析后的命令委托给 TUI、文本流或 JSON 流 | 写入调用方提供的输出 | 依赖组合留给集成阶段
- `ApprovalBroker`: 在策略动作分发与 TUI 之间传递可取消的审批请求和 Y/N 决定 | 异步等待 | 同一 request ID 只允许一个待决审批
- `TerminalState.apply(event)`: 从内核事件派生转录、工具时间线、状态和 Diff 预览 | 无副作用 | 不信任事件中的终端控制字符
- `load_thread_history(reader, thread_id): RestoredThread`: 并发读取一个既存 thread 的消息、事件、目标与 checkpoint，供 TUI 恢复 | SQLite 读取 | 缺失或损坏会话不伪造摘要
- `TerminalState.restore(history)`: 把持久消息和事件投影为固定任务摘要、近期操作与可滚动聊天记录 | 无副作用 | 工具原始输出不进入聊天区
- `WindowsTerminalApp`: 基于 Windows Terminal ANSI 与 `msvcrt` 提供全屏输入、会话选择、审批、Diff 和流式重绘 | 终端 I/O | 首版仅支持 Windows
- `render_terminal(state, input, columns, rows): str`: 生成单帧 ANSI 终端画面 | 无副作用 | 清除控制字符并约束行宽
