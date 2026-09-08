# Windows Application Integration
组合各 Feature 成为 Windows TUI、CLI 和 JSON 模式共用的可运行代理。

## 边界
- 负责：创建共享依赖、调度 typed tools、把策略审批结果传递给文件和运行时 Feature。
- 负责：声明 Windows/PowerShell 执行契约，按工作区能力注册 Git 工具，并把方言不匹配、非零退出码和已知 Git 故障转换为可修复的结构化 tool result。
- 负责：在基础 Provider 提示中声明克制的 Markdown 可读性约定，要求结果前置、短层级和有意义的重点标粗，不以装饰性格式虚构重要性。
- 负责：把 TUI Semantic Insights 控制器绑定到当前 thread 对应 workspace 的共享 `RepoIndexService`，后台刷新同一快照后只读生成用户报告。
- 负责：发布 Chaos Agent 的 `chaos-agent` 命令与本地数据目录，并在迁移期保持旧 `agent` 命令和本地状态可用。
- 不负责：重写 policy、workspace 或 runtime 的底层安全规则。
- 不负责：自动翻译任意 Shell 脚本、自动初始化 Git 仓库或把失败命令报告为成功。
- 不负责：为 Repo Map、bug 定位或 dead-code 分析另建扫描缓存，也不因展示分析结果执行文件修改或验证命令。

## Units
- `TaskScopedVerificationService.suggest_verification(...)`：仅对修改任务或实际变更刷新共享语义图；纯问答收尾直接委托验证服务的非修改路径 | 只读/调度 | 不改动修改任务的证据门，不把未执行的验证标记为通过。
- `ModeAwareWindowsTerminalApp`：从 Interfaces 获取唯一 Muted Slate 主题，委托紧凑启动摘要、双层输入区渲染与动效；完整能力仍通过 `:status` 访问 | 终端输出 | 不修改持久配置或接管 Windows Terminal 设置。
- `build_managed_context`：按冻结 profile 组合 summary/boundary 或 persistent 的上下文、工具和统一预算 client | Sessions/Provider I/O | persistent 不构造 HandoffWriter；模式只投影实际已注册工具，缺省路径不变
- `tool_definitions(include_git, powershell): tuple[ToolDefinition, ...]`: 声明严格且递归校验的契约 loader、工具 schema、generation-aware `read_code_slices`、不可变 edit-plan 契约、冻结 PowerShell 方言和 versioned `run_process_v1`，并按仓库能力省略 Git 工具 | 无副作用 | batch slice 为 1–16 个 target，提示合并当前已知目标但允许新信息后的后续批次；structured process 不接受 shell/env/stdin；文件 auto 不猜 legacy code page
- `windows_system_prompt(...)`：组合冻结的 PowerShell/Git 能力与用户正文的 Markdown 可读性约定 | 无副作用 | 强调仅服务于决策、风险、结果和下一步，不要求逐句装饰
- `RootActionDispatcher`: 在执行前验证 typed schema、评估策略并请求交互审批，再调用文件、编辑、Git 或命令 Unit；`read_code_slices` 先校验当前 RepoIndex generation/snapshot signatures，再委托 Workspace 前后复核 | 产生如实标记成功/失败且保留有界诊断的 tool result | batch 任一 stale 返回 `stale_repo_context` 且不含部分源码；只有已通过策略的外部路径可抵达 workspace Unit
- `run_powershell_action(...)`、`run_process_action(...)`: 分别把冻结方言、默认 Stop 且保留 native 原始字节的脚本和 shell-free `program + args` 映射为 `CommandSpec` | 启动本地进程并失效工作区缓存 | structured process 拒绝 shell launcher、`.cmd/.bat`、NUL 与超限 Windows command line；stdout/stderr 独立严格解码并逐流报告截断，无法解码时返回完整 Base64/code page；legacy script 映射仅供旧 Runtime 兼容
- Windows 路径能力在启动、`/状态` 和 Provider prompt 中可见；入口必须在 `Path.resolve()`、storage mkdir 或进程启动前检查 legacy/extended 预算，不能以“目录不存在”掩盖长路径策略缺失。
- `list_action_result(...)`、`with_action_duration(...)`: Git 工作区根优先使用 tracked/non-ignored-untracked 快速候选清单并再次经过 Workspace 可见性过滤，同时为工具结果补充单调耗时 | Git/文件读取 | 普通目录和显式子目录保留受保护递归扫描；模型侧列表最多 200 条
- `create_application(workspace_root, model_name, profile_name, mode_name): Application`: 在任何 Provider 副作用前真实探测并冻结共享 PowerShell Runtime，再组合模式/profile/model、能力披露策略、权限策略、workspace、会话、插件、子 Agent、provider、TUI 与 CLI | 创建或替换 provider、本地状态和有界子运行时 | source/worktree、主/子 Agent 和 runtime 切换共用同一方言与配置的 capability strategy；Provider 提示不泄露 executable 全路径，本地 `/状态` 可审查完整探测信息；活动任务不可切换
- `ManagedWorkspaceRuntime.close()`、`Application.aclose()`: 在子 Agent、provider 与 MCP 停止后释放所有已物化 workspace 的进程内 Repo Index | 关闭 SQLite 内存连接并清空服务缓存 | 幂等关闭，不删除工作区或持久状态
- `profile_model_factory(...)`、共享 `AttachmentStore` / `AttachmentIngestor`: 将当前 profile 的显式输入模态和产品状态附件 resolver 注入主、子与切换后 provider | 创建有界本地附件仓库 | 测试/自定义单参数 factory 保持旧调用契约；不在集成层解析 blob 或推断模型能力
- `cli.run(...)`、`main()`: 为 ask、resume、JSON run 与 task resume 摄取显式 `--attach`，为仅附件请求补默认提示，并在创建 Application 前处理 help/version；`acp` 分支启动 stdio 适配器；入口把残余 Ctrl+C 归一为退出码 130 | 工作线程文件摄取/命令委托 | ACP stdout 专用于 JSON-RPC，不接收附件或位置参数；Provider 配置错误显示安全的具体原因、配置路径和下一步；不输出 Ctrl+C traceback
- `serve_acp(application)`: 将已组合的 controller、sessions 与 workspace root 注入 ACP Feature 并启动官方 SDK stdio transport | 占用进程 stdin/stdout 至客户端断开 | 不复制 Agent loop，不启用 TUI 交互审批
- `build_application_tui(...)`、`build_foreground(...)`、`build_main_dispatcher(...)`: 组合界面、前台任务和模式限定 dispatcher，并把当前 profile 的显式输入模态校验接入附件草稿 | 读取集成对象并创建 TUI 控制器 | 不实现 Feature 内部行为
- `workspace_uses_repo_map(root, git_available): bool`: 以 broad-root、Git 能力和根目录直属项目标记判定是否自动构建 repo map | 固定探测已知 marker，suffix marker 最多枚举 512 个直属条目 | Home/磁盘根即使是 Git worktree 也保持轻量；不递归扫描
- `Application.foreground_tasks`: 组合前台任务控制器、托管 Git worktree 与同一会话/引擎实例 | Git 工作区任务在 `LOCALAPPDATA/chaos-agent-workspaces` 的短路径托管 worktree 中运行，与受保护的 API 配置目录并列；新 durable snapshot 只写该新根，旧 `chaos-agent/managed-workspaces/snapshots` 仅作为构造时验证、missing-only 的只读 fallback；存储根和仓库中间层不能直接作为 workspace，具体 `worktrees/<repo>/<lineage>` 任务根可用；非 Git 工作区保留 source root；所有 source/worktree Guard 共享启动时冻结的独立敏感路径 opt-in；创建可恢复本地任务，初始化失败的已创建 task 转为可恢复的 interrupted | 不创建、回填、GC 或删除 legacy snapshot 根，不自动迁移旧 worktree，不创建后台 daemon 或自动 Git 提交；Git worktree 创建失败不得静默退回 source root
- `TaskWorkspace`、`WorkspaceServices`: 在独立模型模块中承载 lineage/worktree 身份与已组合服务，供 Host composition 和 runtime 共用 | 无副作用 | 不自行创建 Guard、进程或持久状态
- `build_mode_registry(...)`、`freeze_mode(...)`：绑定四档模式的实际主 profile、统一完整工具集、统一 Ultra 硬预算、提示策略和推理强度 | 读取显式环境绑定 | profile 不存在时拒绝启动，不回退到任意模型。
- `RuntimeSelectionControl.use(...)`、`resolve_runtime_contract(...)`: 在空闲边界独立切换/恢复 topology、已配置 profile 与 reasoning effort，并原子替换 provider、runner、dispatcher 与审计快照 | 创建并关闭 provider client | legacy/plugin mode 只更新提示/工具投影和 `legacy_mode`，不得重置三个独立选择轴；构建或 runner 替换失败保留旧 runtime；恢复时 model/protocol/host/digest 任一漂移均失败闭合
- `runtime_selection_control.py`、`runtime_dispatcher_factory.py`、`runtime_provider_controls.py`：分别承载选择 API、主/子工具投影和 provider/runtime 恢复，`runtime_controls.py` 只保留兼容导出与组合 | 进程内状态/provider 生命周期 | 每个 Python Unit 保持不超过 300 行，TUI 同时兼容 `profiles()` 与 `list_profiles()` 目录入口
- `runtime_client_cleanup.py`：回收 client 已创建但 context/runner 构造失败的 partial runtime | provider client 关闭或异步 retirement | 主 runtime 在重抛任意 `BaseException` 前等待 best-effort close；同步 child factory 保留并调度异步 close，清理失败不得覆盖原构造失败
- runtime task identity：TaskContract 最后一个 digest 冻结完整 `ModeSnapshot`（含实际 tool set）而不只冻结 provider 选择 | 无副作用 | plugin mode 恢复无法重建同一收窄 snapshot 时失败闭合，绝不回落到更宽的内置 mode
- `profile_model_factory(...)` 的 Anthropic capability 映射：默认 `medium` 明确显示为 prompt-only，并在没有确认协议字段时向 `AnthropicClient` 传 `None` | 创建 provider client | 非默认 effort 选择及携带非默认 effort 切入 Anthropic 均在 runtime 重建前拒绝；不猜测 reasoning wire 字段，profile `max_output_tokens` 仍映射为 `max_tokens`
- `RestrictedDispatcher(..., allow_delegation=..., allow_coordination=...)`: 按冻结 topology 投影主 Agent 工具，并在 Host 存在时保留无权限扩张作用的契约 loader | 契约读取在 wrapper 内按投影后快照解析 | 仅 team 主 Agent 可见 `delegate_agent`；single/team 主 Agent 可见已注册的 `list_agents`/`send_message`，所有 child dispatcher 禁止委派与 peer 协调，`rename_agent` 永不暴露给模型
- `child_mode_for_role(...)`、`SubagentTool.dispatch(...)`、`EngineChildRunner.run(...)`：按 Search/Librarian→Low、Subagent→Medium、Review/Oracle→High 路由 profile，并把 `delegate_agent` 转换为受预算、取消和单写者约束的真实子 thread | provider/session/tool 调用 | 路由不依赖父模式；子结果始终 advisory，不产生 verification evidence。
- `load_plugins(...)`、`PluginToolBridge`：发现可信 manifest 并把不可变工具贡献映射到 Host typed action | 读取 manifest/信任文件 | 插件风险与目标 action 风险必须分别通过中央策略。
- `build_context_runtime(...)`、`PersistingAnchoredCompactor.compact(...)`：组合共享的本地确定性压缩器与语义压缩，并在 Host 持久化脱敏 checkpoint facts | 仅 Host 写入固定八项元数据 | Context Feature 不持久化；revision、summary、source text 和 messages 不进入 payload，持久化错误与取消原样传播。
- `TaskScopedVerificationService`：按 task/workspace 复用验证事务，并从活动 Context 的同一 `RepoIndexSnapshot` 刷新 Planner | 维护进程内 task 到 service 绑定并触发 milestone/final verifier | workspace root 漂移失败闭合；不另建 RepoIndex，不接受模型伪造验证 scope
- `WorkspaceEditPlanStore`、`create_stored_edit_plan(...)`、`WorkspaceEditPlanActions`、`edit_plan_results`：把模型提供的多文件意图转换为 Host 所有的不可变计划，以 ID/digest 分离 plan 与 apply，并显式编码工作区是否可能已变化及涉及路径 | 进程内保存有界计划、只读 Git tracked/dirty 状态和工作区预览 | apply 前重新 preflight；Git ignored/untracked 既有文件必须标记显式风险；计划只能消费一次，取消前不得进入 applying；完整回滚、预检冲突和拒绝不得宣称工作区变化，模型不能提交风险标志或可信 Diff
- `WorkspaceMutationPool`：按 canonical workspace root 复用 editor、快照、gate、capture、计划仓库和 coordinated session facade | 延迟创建每个 source/task root 的持久状态依赖 | source 与 task worktree 绝不共用 mutation gate、snapshot 或计划；所有 facade 共用同一个基础 Sessions 仓库
- `WorkspaceMutationGate`、`RewindCaptureCoordinator`、`CoordinatedSessionRepository`：共享一个基础回溯仓库并严格排序 mutation/checkpoint，批次 apply 在首次写前持久化 PRE/POST journal | 写入会话日志、快照和工作区 | 未知写者先持久化 gap；异常或取消先完成 owned-only 恢复再释放 gate，foreign 结果持久化为 conflict 并阻止后续写入
- `recover_workspace_edit_batches(...)`：启动时逐个恢复 source/task root 中未闭合的编辑批次，再允许普通 checkpoint rewind 恢复 | 读取 durable journal 并按 workspace 执行恢复 | 并发启动只执行一次；任一 foreign conflict 立即失败闭合，不继续其他工作区写入
- `WorkspaceSessionRouter`: 透传唯一 `RewindSessionRepository`，按 thread/owner 所属 workspace 把 checkpoint 路由到该 root 的 `CoordinatedSessionRepository` | 仅被选中的 facade 写入 checkpoint/coverage | 未绑定 child 回退 owner root，再回退 source root；任何 facade 必须共享同一基础仓库
- `RewindRuntime`：基于基础仓库和同一快照存储生成双观测只读预览 | 只读会话、快照和工作区 | 不提供 apply、restore、approval、provider 或 Git reset。
- `ModeAwareWindowsTerminalApp.update_capability(...)`、`GitDiffAdapter`：展示并刷新模式/权限分离信息，并把 staged/unstaged/untracked 快照交给只读 Diff 交互，把 `/rewind` 仅委托给 Interfaces handler | 终端/Git 读取 | mode 切换不修改权限；diff 和回溯预览不修改 Git index。
- `SystemDoctor.run()`、`TaskCostControl.report(...)`：分别执行有界 host/TCP 诊断和持久预算费用投影 | 只读系统、网络探测与 Sessions 读取 | 不发送 API key 或 HTTP 请求；未配置 profile 价格时只报告真实 token，不估算费用
- `SemanticGraphControl.analyze(...)`：按活动 thread 解析 source/worktree root，并在线程外请求该 root 共享 `RepoIndexService` reconcile 后生成 Semantic Insights 报告，query/FTS 与图取自同代快照 | 只读 Repo Index 与 workspace 绑定 | 不实例化第二索引；未绑定 thread 回退 source root；报告明示 root、generation 与索引上限，支持分页但不改变分析范围
- `configure_product_controls(...)`、`configure_product_ui(...)`：组合 runtime、cost、doctor、semantic graph 与 TUI/foreground 控件 | 创建进程内依赖 | 从 app.py 抽离产品组合，不复制任何 Feature 行为
- `PeerToolAdapter`、`PEER_TOOL_DEFINITIONS`: 独立暴露 `list_agents`、`send_message`、`rename_agent` typed tools | 仅委托已注册的 PeerMessagingService | 本 Unit 不接 Root dispatcher/UI，错误输出不回显正文，peer 输入仍不具有用户授权
- `PeerRuntime`、`PeerDeliveryBuffer`、`PeerContextBuilder`: 仅随 Windows TUI 注册本机实例，续租并投递 queued 消息、提示 held 消息，把 PEER 正文以有界不可信 JSON 注入主上下文 | SQLite/模型回合/TUI 元数据 | task-owned thread 不后台恢复；taskless peer 回合只开放 list/send；失败指数退避，runtime 切换与 peer wake 共用 activity lock
