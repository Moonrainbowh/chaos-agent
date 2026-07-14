# User Interfaces
通过同一内核提供终端优先的追加式转录、单次 CLI 和机器可读命令模式。

## 边界
- 负责：追加式终端转录、底部输入和状态尾部、带可信语义样式的显示项、斜杠命令调色板、审批提示、Diff 预览、状态显示、会话选择和 JSON 输出。
- 负责：`agent`、`agent ask`、`agent resume` 与 `agent run --json` 的一致用户语义。
- 负责：以 Windows Terminal 和 PowerShell 的原生文本选择、复制、滚轮回滚、键盘输入、Unicode、颜色及窗口调整为首版交互验收基线。
- 负责：将完成的消息、工具和任务结果追加到普通终端缓冲区；仅重绘可变高度的圆角实线输入区和其下方的一行紧凑状态。
- 负责：输入区以 `Enter` 提交、`Ctrl+J` 插入换行，并在多行内容中提供符合行结构的光标移动。
- 负责：默认使用 `›`、`◆`、`↳`、`✓`、`!`、`×` 等 Unicode 符号，ASCII 仅作为显式兼容回退；不使用 Emoji 或混合两套符号语法。
- 负责：普通用户和 Agent 正文使用高可读的白色；Chaos 品牌、用户标记、选中项、标题、路径、内联代码和 Diff 增加使用青色强调。
- 负责：工具调用、元数据和次要状态使用低对比暗灰色；绿色仅用于任务级且证据充分的真正完成，不用于普通 Action 完成、部分完成或未验证结果。
- 负责：采用中等留白：对话轮次之间空一行，连续工具记录不空行，工具组与最终结论之间空一行，短正文内部不额外扩张间距。
- 负责：从不可信模型或工具文本中剥离终端控制序列；ANSI 样式只能由本地可信显示事件生成，并支持 `auto`、`always`、`never` 颜色模式及 ASCII 回退。
- 负责：当输入以 `/` 开头时在输入框上方显示最多五条可过滤命令候选，候选不进入状态行或普通滚动区；命令只委托注入的控制器或只读服务，不直接执行工具、修改配置或调用 provider。
- 负责：底部状态栏左侧显示动态任务状态，右侧显示模型、耗时等稳定上下文；窗口变窄时按优先级隐藏右侧信息，动态文本不得左右跳动。
- 不负责：复制 Agent 状态机、直接执行工具、直接访问 provider、切换活动任务的模型，或绕过 `ActionPolicy` 权限决定。
- 不负责：接管 Windows Terminal 字体、调色板、复制设置或鼠标选择；不提供默认全屏仪表板、固定顶部栏或应用自有滚动历史。
- 不负责：首版 Linux/macOS 端到端适配、IDE 插件、Web UI、远程多用户服务或桌面应用。
- 同一 TUI 同时只管理一个前台任务；运行中的普通输入作为 steering 排队，而非创建第二任务。
- `Esc` 暂停并创建 checkpoint，关闭 TUI 中断并创建 checkpoint，显式停止转为失败；语言切换只替换 UI catalog，不改变模型上下文或工作区文件。
- 负责在单栏转录中呈现 evidence-backed completion、partial/unverified 差异、当前未满足条件及 `/证据`、`/evidence` 查询；不自行判定验证成功。

## Units
- `format_evidence_summary(records)`: 渲染有界 evidence 摘要 | 无副作用 | 不将 UI 文本升级为完成裁决
- `ForegroundTaskController.accept_partial(...)`: 持久化用户明确接受的未完整验证交付 | 写入 task/checkpoint | 仅 `VERIFYING` 或 `WAITING_DECISION` 可进入该终态
- `AgentController`、`ForegroundTaskController`: 向交互和非交互调用暴露同一核心事件与前台任务控制 | 调用内核 | 仅实际 created/running 执行阻止并发启动，paused/interrupted 等可恢复记录不得永久锁死新会话
- `ForegroundTaskController.interrupt(task_id, reason)`: 取消活动 token 并持久化 `INTERRUPTED` checkpoint | SQLite I/O | 不在 runner 返回时兜底完成任务
- `parse_command`、`execute_command`: 解析并委托稳定 CLI 语义，文本命令复用可信 Markdown 终端渲染 | 写入调用方输出 | 不直接输出模型 Markdown 控制标记，不直接退出或组合依赖
- `parse_tui_command(text)`、`filter_palette(input)`: 解析斜杠命令并筛选已接入的候选 | 无副作用 | 以结构化错误恢复，不显示占位或危险命令
- `InputBuffer`: 编辑原始 Windows 键盘输入、多行光标、历史与清空快捷键 | 进程内状态 | `Enter` 提交、`Ctrl+J` 换行，不接管终端选择和回滚
- `DisplayEntry`、`DisplaySpan`、`TerminalState`: 将事件投影为最终回答、工具完成行和每轮独立的执行摘要 | 无副作用 | 完整 assistant 消息可在任务验证状态前落屏，不保留或渲染原始 reasoning
- `render_entry`、`render_entries`、`render_live_tail_frame`、`status_presentation`、`read_key`: 生成可信 ANSI 转录、Unicode/ASCII 回退、列宽自适应 Markdown、候选命令、双区状态尾部和 Windows 原始按键投影 | 无副作用（除读取按键） | 只擦除动态尾部，无全屏清除或鼠标跟踪序列
- `WindowsTerminalApp`: 追加完成条目、继续当前未终结前台任务并维护输入/状态尾部 | 终端 I/O | 可恢复的任务启动竞争显示为带内错误，不退出 TUI；不接管终端的选择或回滚
- `ApprovalBroker`、`load_thread_history`: 提供可取消审批和已保存会话读取 | 异步/SQLite 读取 | 不伪造会话摘要
