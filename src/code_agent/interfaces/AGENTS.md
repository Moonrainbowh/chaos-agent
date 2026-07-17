# User Interfaces
通过同一内核提供终端优先的追加式转录、单次 CLI 和机器可读命令模式。

## 边界
- 负责：追加式终端转录、底部输入和状态尾部、带可信语义样式的显示项、斜杠命令调色板、审批提示、Diff 预览、状态显示、会话选择和 JSON 输出。
- 负责：`agent`、`agent ask`、`agent resume` 与 `agent run --json` 的一致用户语义。
- 负责：以 Windows Terminal 和 PowerShell 的原生文本选择、复制、滚轮回滚、键盘输入、Unicode、颜色及窗口调整为首版交互验收基线。
- 负责：将完成的消息、工具和任务结果追加到普通终端缓冲区；仅重绘可变高度的圆角实线输入区和其下方的一行紧凑状态。
- 负责：输入区以 `Enter` 提交、`Ctrl+J` 插入换行，并在多行内容中提供符合行结构的光标移动。
- 负责：把普通按键、粘贴块与 Windows 控制信号归一为结构化输入事件；粘贴块规范化换行、有界插入且绝不自动提交。
- 负责：以可注入时钟实现双击 `Ctrl+C` 退出保护；第一次按当前状态暂停、拒绝、清空或提示，只有两秒内第二次才退出。
- 负责：默认使用 `›`、`◆`、`↳`、`✓`、`!`、`×` 等 Unicode 符号，ASCII 仅作为显式兼容回退；不使用 Emoji 或混合两套符号语法。
- 负责：普通用户和 Agent 正文使用高可读的白色；Chaos 品牌、用户标记、选中项、标题、路径、内联代码和 Diff 增加使用青色强调。
- 负责：工具调用、元数据和次要状态使用层级较弱但清晰可读的中灰色；关联 Action request/result 并只展示脱敏、有界的工具事实摘要；绿色仅用于任务级且证据充分的真正完成，不用于普通 Action 完成、部分完成或未验证结果。
- 负责：将 Markdown 表格渲染为无纵线的三线表，窄宽度改用稳定逐项布局。
- 负责：采用中等留白：对话轮次之间空一行，连续工具记录不空行，工具组与最终结论之间空一行，短正文内部不额外扩张间距。
- 负责：从不可信模型或工具文本中剥离终端控制序列；ANSI 样式只能由本地可信显示事件生成，并支持 `auto`、`always`、`never` 颜色模式及 ASCII 回退。
- 负责：从单一命令注册表生成解析、帮助、调色板、补全、显示标签与动态可用性；命令只委托注入的控制器或只读服务，不直接执行工具、修改配置或调用 provider。
- 负责：输入 `/` 时完整展示当前可用的一级命令目录；选中无参数命令后一次 `Enter` 立即执行，选中复合命令后一次 `Enter` 进入由同一注册表生成的二级动作菜单，不要求重复确认同一选择。
- 负责：命令二级菜单以结构化动作声明模型、Skills、MCP、任务和显示设置等合法操作；返回、取消、禁用原因和缺少后续实体选择均保持可见，不把自由文本参数伪装成可枚举菜单。
- 负责：底部状态栏左侧显示动态任务状态，右侧显示模型、耗时等稳定上下文；窗口变窄时按优先级隐藏右侧信息，动态文本不得左右跳动。
- 不负责：复制 Agent 状态机、直接执行工具、直接访问 provider、切换活动任务的模型，或绕过 `ActionPolicy` 权限决定。
- 不负责：接管 Windows Terminal 字体、调色板、复制设置或鼠标选择；不提供默认全屏仪表板、固定顶部栏或应用自有滚动历史。
- 不负责：首版 Linux/macOS 端到端适配、IDE 插件、Web UI、远程多用户服务或桌面应用。
- 同一 TUI 同时只管理一个前台任务；运行中的普通输入作为 steering 排队，而非创建第二任务。
- `Esc` 暂停并创建 checkpoint，关闭 TUI 中断并创建 checkpoint，显式停止转为失败；语言切换只替换 UI catalog，不改变模型上下文或工作区文件。
- 负责在单栏转录中呈现 evidence-backed completion、partial/unverified 差异、当前未满足条件及 `/证据`、`/evidence` 查询；不自行判定验证成功。
- 负责：在输入区上方提供键盘可操作的上下文 Picker；命令、会话、模式/模型、Skills、MCP 和插件贡献共用选择、过滤、补全、禁用原因与错误恢复语义。
- 负责：明确分开展示 Agent 模式与访问权限，并显示模式对应的实际模型、Oracle、成本/延迟定位和下一任务生效边界。
- 负责：运行中普通提交仍作为同一前台任务的 steering，在转录和状态尾部显示 queued、steered、dequeued、applied 及队列数量；强制中断与安全边界注入保持独立语义，状态只依据内核持久事件更新。
- 负责：以类型化状态快照呈现 running、verifying、paused、waiting decision、approval、partial 和 completed，不得把非空闲状态统一显示为“就绪”。
- 负责：审批和结构化用户选择使用可取消的键盘交互，明确显示动作、风险、目标、选项和 `Enter`/`Esc` 结果，不在不可见状态下等待输入。
- 负责：统一渲染 Host 与插件声明的 `notify`、`confirm`、`input`、`select` 交互原语；插件不得直接生成终端控制序列或替用户回答。
- 负责：以紧凑追加行呈现工具和子 Agent 生命周期、目标、耗时、结果及有界事实；详细内容通过稳定 ID 按需查询，不重写历史滚动区。
- 负责：父任务运行期间持续投影每个子 Agent 的 queued、running、completed、failed 和 cancelled 状态、角色、目标摘要、耗时及有界用量；父任务中断是正常控制结果，不显示异常类名或通用错误。
- 负责：从类型化写入结果和注入的只读 Git 服务生成 diff 统计与有界 unified diff；支持文件/区块导航、评论反馈、路径过滤、窄屏布局和 `NO_COLOR` 回退。
- 负责：diff/rewind views 仅为只读投影，严格区分 preview 与 apply；不运行 Git、不授予权限、不重写用户历史。
- 负责：stage/unstage 等改变 Git 索引的操作只能由显式用户命令委托 typed Git action，并经过与其他写动作相同的策略、审批和审计；界面不直接调用 Git。
- 负责：会话 Picker 显示标题、预览、状态、更新时间和消息数；读取与恢复语义分离，超长 Thread 使用分页、窗口读取和搜索。
- 负责：模式、模型、Skills、MCP 和插件操作显示当前值、来源、信任、健康、风险与生效边界，不接收密钥、任意 URL 或启动命令。
- 负责：原始 reasoning 不进入转录、状态或会话；只呈现本地生命周期推导的活动标签，或 provider 明确标注、脱敏且有界的 reasoning summary。
- 负责：维持一个前台父任务、追加式单栏转录和最小动态尾部；子 Agent 是该任务内的层级执行，不引入默认全屏、多栏、固定侧栏或卡片化仪表盘。
- 不负责：调度子 Agent、生成语义摘要、执行插件提案，或把 mode、Oracle、插件和 UI 选择解释为权限授权或 verification evidence。

- 负责：纯 rewind 模型、稳定禁用原因、候选分页、只读 source 委托和有界安全渲染。
- 不负责：Sessions 查询、snapshot 加载、文件恢复、Git 操作、apply 授权或 provider 调用。

## Units
- `format_evidence_summary(records)`: 渲染有界 evidence 摘要 | 无副作用 | 不将 UI 文本升级为完成裁决
- `ForegroundTaskController.accept_partial(...)`: 持久化用户明确接受的未完整验证交付 | 写入 task/checkpoint | 仅 `VERIFYING` 或 `WAITING_DECISION` 可进入该终态
- `AgentController`、`ForegroundTaskController`: 向交互和非交互调用暴露同一核心事件与前台任务控制 | 调用内核 | 仅实际 created/running 执行阻止并发启动，paused/interrupted 等可恢复记录不得永久锁死新会话
- `ForegroundTaskController.interrupt(task_id, reason)`: 取消活动 token 并持久化 `INTERRUPTED` checkpoint | SQLite I/O | 不在 runner 返回时兜底完成任务
- `parse_command`、`execute_command`: 解析并委托稳定 CLI 语义，文本命令复用可信 Markdown 终端渲染 | 写入调用方输出 | 不直接输出模型 Markdown 控制标记，不直接退出或组合依赖
- `parse_tui_command(text)`、`filter_palette(input)`: 解析斜杠命令并筛选已接入的候选 | 无副作用 | 以结构化错误恢复，不显示占位或危险命令
- `CommandRegistry`: 声明命令、别名、参数、依赖、可用性与执行委托 | 无副作用 | 是解析、帮助、调色板和补全的唯一目录
- `CommandAction`、`CommandRegistry.resolve(...)`：声明并解析复合命令的二级动作 | 无副作用 | 只列举真实接通动作，需要自由文本参数时保留输入边界
- `InputEvent`、`ExitGuard`: 归一键盘/粘贴/控制信号并实施双击退出状态机 | 进程内状态 | 粘贴不提交，状态机可注入时钟
- `InputBuffer`: 编辑原始 Windows 键盘输入、多行光标、历史与清空快捷键 | 进程内状态 | `Enter` 提交、`Ctrl+J` 换行，不接管终端选择和回滚
- `DisplayEntry`、`DisplaySpan`、`TerminalState`: 将事件投影为最终回答、工具完成行和每轮独立的执行摘要 | 无副作用 | 完整 assistant 消息可在任务验证状态前落屏，不保留或渲染原始 reasoning
- `render_entry`、`render_entries`、`render_live_tail_frame`、`status_presentation`、`read_key`: 生成可信 ANSI 转录、Unicode/ASCII 回退、列宽自适应 Markdown、候选命令、双区状态尾部和 Windows 原始按键投影 | 无副作用（除读取按键） | 只擦除动态尾部，无全屏清除或鼠标跟踪序列
- `WindowsTerminalApp`: 追加完成条目、继续当前未终结前台任务并维护输入/状态尾部 | 终端 I/O | 可恢复的任务启动竞争显示为带内错误，不退出 TUI；不接管终端的选择或回滚
- `ApprovalBroker`、`load_thread_history`: 提供可取消审批和已保存会话读取 | 异步/SQLite 读取 | 不伪造会话摘要
- `PickerState`、`PickerItem`：统一命令、会话、模式、Skill、MCP 和插件候选的过滤、键盘选择、补全和禁用原因 | 进程内状态 | `Esc` 取消，不执行候选动作。
- `SteeringQueueView`：投影 queued、steered、dequeued、applied 及队列数量 | 进程内状态 | 只依据持久 `TURN_STARTED`、`CONTEXT_BUILT` 边界推进消费和应用状态。
- `HostInteraction`、`InteractionBroker`、`plugin_interaction`：统一 Host 与插件的 `notify`、`confirm`、`input`、`select` 请求及可取消结果 | 异步状态 | 插件请求转换为 Host 所有的交互，不接受预填用户答案。
- `DiffController.load(...)`、`DiffView`：按 working tree/facet/turn/checkpoint scope 投影 recorded 或只读 Git unified diff，并保留来源、新鲜度、stale、文件导航、路径过滤和 scope 评论 | 只读服务调用/进程内状态 | recorded scope 不调用 live source；不直接 stage、unstage 或执行 Git。
- `ModePermissionView`：分开展示模式实际模型、Oracle、推理强度、生效边界与访问权限 | 无副作用 | 模式信息绝不解释为授权。
- `TuiInteractions`：把 Picker、可见审批、steering 生命周期和结构化 diff 委托给单栏 TUI | 终端显示/进程内状态 | 审批默认拒绝，`Enter` 明确选择，`Esc` 取消。
- `AgentRunStatusProjection.observe(view)`：将子 Agent 状态变化投影为去重、有界的生命周期行 | 进程内状态 | 只消费 Orchestration 快照，不从工具名称猜测状态。
