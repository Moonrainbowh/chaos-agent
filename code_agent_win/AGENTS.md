# Windows Application Integration
组合各 Feature 成为 Windows TUI、CLI 和 JSON 模式共用的可运行代理。

## 边界
- 负责：创建共享依赖、调度 typed tools、把策略审批结果传递给文件和运行时 Feature。
- 负责：声明 Windows/PowerShell 执行契约，按工作区能力注册 Git 工具，并把方言不匹配、非零退出码和已知 Git 故障转换为可修复的结构化 tool result。
- 负责：发布 Chaos Agent 的 `chaos-agent` 命令与本地数据目录，并在迁移期保持旧 `agent` 命令和本地状态可用。
- 不负责：重写 policy、workspace 或 runtime 的底层安全规则。
- 不负责：自动翻译任意 Shell 脚本、自动初始化 Git 仓库或把失败命令报告为成功。

## Units
- `tool_definitions(include_git): tuple[ToolDefinition, ...]`: 声明严格工具 schema 与 PowerShell 方言提示，并按仓库能力省略 Git 工具 | 无副作用 | 非 Git 工作区不得向模型暴露 `git_status`、`git_diff`
- `RootActionDispatcher`: 在执行前验证 PowerShell 方言、评估策略并请求交互审批，再调用 typed 文件、编辑、Git 或命令 Unit | 产生如实标记成功/失败且保留有界诊断的 tool result；成功编辑通知精确 dirty path，命令和验证通知一次全量 reconciliation | 只有已通过策略的外部路径可抵达 workspace Unit
- `create_application(workspace_root, model_name, profile_name, mode_name): Application`: 冻结并组合模式/profile/model、策略、workspace、会话、插件、子 Agent、provider、TUI 与 CLI，创建主/子 Agent 和模式切换共同复用的单一进程内 Repo Index，并允许 TUI 在空闲边界按已配置 mode 原子重建主 runner | 创建或替换 provider、本地状态和有界子运行时 | 活动任务不可切换；mode 改变模型、推理和编排但不改变访问权限；Home/磁盘根始终关闭自动 repo map；Repo Index 不落盘。
- `build_application_tui(...)`、`build_foreground(...)`、`build_main_dispatcher(...)`: 组合界面、前台任务和模式限定 dispatcher | 读取集成对象并创建 TUI 控制器 | 不实现 Feature 内部行为
- `workspace_uses_repo_map(root, git_available): bool`: 以 broad-root、Git 能力和根目录直属项目标记判定是否自动构建 repo map | 固定探测已知 marker，suffix marker 最多枚举 512 个直属条目 | Home/磁盘根即使是 Git worktree 也保持轻量；不递归扫描
- `Application.foreground_tasks`: 组合前台任务控制器和同一会话/引擎实例 | 创建可恢复本地任务 | 不创建后台 daemon、worktree 或自动 Git 提交
- `build_mode_registry(...)`、`freeze_mode(...)`：绑定四档模式的实际主 profile、统一完整工具集、统一 Ultra 硬预算、提示策略和推理强度 | 读取显式环境绑定 | profile 不存在时拒绝启动，不回退到任意模型。
- `child_mode_for_role(...)`、`SubagentTool.dispatch(...)`、`EngineChildRunner.run(...)`：按 Search/Librarian→Low、Subagent→Medium、Review/Oracle→High 路由 profile，并把 `delegate_agent` 转换为受预算、取消和单写者约束的真实子 thread | provider/session/tool 调用 | 路由不依赖父模式；子结果始终 advisory，不产生 verification evidence。
- `load_plugins(...)`、`PluginToolBridge`：发现可信 manifest 并把不可变工具贡献映射到 Host typed action | 读取 manifest/信任文件 | 插件风险与目标 action 风险必须分别通过中央策略。
- `build_context_runtime(...)`、`PersistingAnchoredCompactor.compact(...)`：组合共享的本地确定性压缩器与语义压缩，并在 Host 持久化脱敏 checkpoint facts | 仅 Host 写入固定八项元数据 | Context Feature 不持久化；revision、summary、source text 和 messages 不进入 payload，持久化错误与取消原样传播。
- `WorkspaceMutationGate`、`RewindCaptureCoordinator`、`CoordinatedSessionRepository`：共享一个基础回溯仓库并严格排序 mutation/checkpoint | 写入会话日志和工作区 | 未知写者先持久化 gap。
- `RewindRuntime`：基于基础仓库和同一快照存储生成双观测只读预览 | 只读会话、快照和工作区 | 不提供 apply、restore、approval、provider 或 Git reset。
- `ModeAwareWindowsTerminalApp.update_capability(...)`、`GitDiffAdapter`：展示并刷新模式/权限分离信息、提供只读实时 diff，并把 `/rewind` 仅委托给 Interfaces handler | 终端/Git 读取 | mode 切换不修改权限；diff 和回溯预览不修改 Git index。
