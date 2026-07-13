# User Interfaces
通过同一内核提供终端优先的追加式转录、单次 CLI 和机器可读命令模式。

## 边界
- 负责：追加式终端转录、底部输入和状态尾部、带可信语义样式的显示项、斜杠命令调色板、审批提示、Diff 预览、状态显示、会话选择和 JSON 输出。
- 负责：`agent`、`agent ask`、`agent resume` 与 `agent run --json` 的一致用户语义。
- 负责：以 Windows Terminal 和 PowerShell 的原生文本选择、复制、滚轮回滚、键盘输入、Unicode、颜色及窗口调整为首版交互验收基线。
- 负责：将完成的消息、工具和任务结果追加到普通终端缓冲区；仅重绘输入行和其下方的一行紧凑状态。
- 负责：从不可信模型或工具文本中剥离终端控制序列；ANSI 样式只能由本地可信显示事件生成，并支持 `auto`、`always`、`never` 颜色模式及 ASCII 回退。
- 负责：当输入以 `/` 开头时显示可过滤命令调色板；命令只委托注入的控制器或只读服务，不直接执行工具、修改配置或调用 provider。
- 不负责：复制 Agent 状态机、直接执行工具、直接访问 provider、切换活动任务的模型，或绕过 `ActionPolicy` 权限决定。
- 不负责：接管 Windows Terminal 字体、调色板、复制设置或鼠标选择；不提供默认全屏仪表板、固定顶部栏或应用自有滚动历史。
- 不负责：首版 Linux/macOS 端到端适配、IDE 插件、Web UI、远程多用户服务或桌面应用。

## Units
- `AgentController`、`ForegroundTaskController`: 向交互和非交互调用暴露同一核心事件与前台任务控制 | 调用内核 | 不重解释动作或绕过取消令牌
- `parse_command`、`execute_command`: 解析并委托稳定 CLI 语义 | 写入调用方输出 | 不直接退出或组合依赖
- `parse_tui_command(text): ParseOutcome`: 解析斜杠命令并以结构化错误恢复 | 无副作用 | 不抛出用户输入错误
- `filter_palette(input): tuple[PaletteItem, ...]`: 筛选已接入的斜杠命令目录 | 无副作用 | 不显示占位或危险命令
- `InputBuffer`: 编辑原始 Windows 键盘输入、历史与清空快捷键 | 进程内状态 | 不接管终端选择和回滚
- `DisplayEntry`、`DisplaySpan`、`TerminalState`: 将事件投影为最终回答、工具完成行和每轮独立的执行摘要 | 无副作用 | 不保留或渲染原始 reasoning
- `render_entry`、`render_live_tail`: 生成可信 ANSI 条目、列宽自适应 Markdown 表格和仅两行的动态状态尾部 | 无副作用 | 无全屏清除或鼠标跟踪序列
- `WindowsTerminalApp`: 追加完成条目并维护输入/状态尾部 | 终端 I/O | 不接管终端的选择或回滚
- `ApprovalBroker`、`load_thread_history`: 提供可取消审批和已保存会话读取 | 异步/SQLite 读取 | 不伪造会话摘要
