# Action Policy
用统一能力模型评估每个外部动作，并产生可解释、可审计的允许、拒绝或询问决定。

## 边界
- 负责：计划/执行模式、风险分级、路径与命令规则、环境变量白名单、网络策略和审批请求。
- 负责：记录决策依据，并保证只读、写入、命令和外部访问使用一致的策略入口。
- 不负责：执行文件或进程操作、保存会话、调用模型或实现交互界面。
- 不负责：把本机执行描述为操作系统级沙箱；强隔离由 Runtime adapter 提供。
- 默认访问级别为 `auto`；任务授权不得自动允许模型提供的任意 PowerShell 文本或结构化进程，只有参数受限、由本地 adapter 生成 argv 的 typed verification 可在匹配的本地授权下自动执行。
- 显式 `unrestricted` 可自动允许已识别的非 critical 原始命令、网络和工作区外动作；类型化动作显式指向 protected path 时仍要求审批，敏感文件还必须通过 Workspace 的独立显式 opt-in。该 opt-in 只约束类型化文件工具，不把原始 shell 变为 OS 沙箱。critical 动作与未知工具始终拒绝，其他访问级别保持拒绝或逐次审批，且不能由任务授权降级。
- MCP 工具必须按本地配置显式映射为 read、write、network 或 critical；server 自报只读属性不能替代映射，未知映射保持拒绝。
- Agent 模式、Subagent、Oracle 和插件不得直接授予权限；它们只能在现有用户授权与任务授权内增加约束，所有外部动作仍使用同一策略入口。
- 插件风险声明只能提高或补充本地风险分类，不能降低风险、替用户批准动作或绕过 protected、unknown、critical 与 network 边界。

## Units
- `ApprovalMode`、`Capability`、`DecisionOutcome`、`RiskLevel`、`PolicyDecision`: 表达稳定的审批、能力、结果与风险词汇以及不可变决定 | 无副作用 | `auto` 是默认访问级别；`unrestricted` 只能显式选择且不能绕过类型化 protected、critical 与 unknown 边界
- `RAW_SHELL`、`RAW_PROCESS`、`VERIFICATION`、`PROTECTED_PATH`、`EXPLICIT_APPROVAL`: 区分模型原始 shell、shell-free 结构化进程、受信验证、受保护路径和必须显式确认的计划风险 | 无副作用 | task grant 只可自动允许 `VERIFICATION`，不能绕过 dirty、Git ignored/untracked 既有文件、delete/move/case-only edit plan 的确认
- `requires_explicit_edit_plan_approval(risk_flags)`: 严格验证 Host 注入的本地计划风险并决定是否必须询问 | 无副作用 | 只接受已知风险 flag（包括非 Git 既有文件与 Git 仓库中 ignored/untracked 既有文件）；`ActionPolicy.evaluate(..., trusted_edit_risk_flags=...)` 仅对 apply 工具生效，PLAN 仍拒绝写入
- `classify_action(request, workspace_root): ActionClassification`: 从工具名、递归路径参数和 raw/argv 命令信号生成能力与风险提示 | 解析路径但不写入 | 路径执行 workspace containment；共享命令检查是保守启发式，不是 shell parser
- `PolicyConfig`、`ActionPolicy.evaluate(request): PolicyDecision`: 以不可变模式、网络开关和 workspace root 执行访问级别决策表 | 无副作用 | 默认 `auto`；protected path 在 `unrestricted` 之前进入审批，critical 与未知工具始终拒绝
- `ActionPolicy.evaluate(request, task_authorization)`: 在有效任务授权下允许普通工作区读写和 Host 固定 argv 的 typed verification | 无副作用 | raw shell、raw process、unknown、critical、network、outside-workspace 不被任务授权自动放行
- `sanitize_environment(host_env, allowed_names, explicit_env): dict`: 生成 Windows 子进程最小环境白名单 | 无副作用 | 名称不区分大小写，显式值仅限批准名称
- `redact_sensitive(value): value`: 递归复制并遮盖敏感键对应的值 | 无副作用 | 不修改输入
