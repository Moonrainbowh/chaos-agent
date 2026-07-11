# Action Policy
用统一能力模型评估每个外部动作，并产生可解释、可审计的允许、拒绝或询问决定。

## 边界
- 负责：计划/执行模式、风险分级、路径与命令规则、环境变量白名单、网络策略和审批请求。
- 负责：记录决策依据，并保证只读、写入、命令和外部访问使用一致的策略入口。
- 不负责：执行文件或进程操作、保存会话、调用模型或实现交互界面。
- 不负责：把本机执行描述为操作系统级沙箱；强隔离由 Runtime adapter 提供。

## Units
- `ApprovalMode`、`Capability`、`DecisionOutcome`、`RiskLevel`、`PolicyDecision`: 表达稳定的审批、能力、结果与风险词汇以及不可变决定 | 无副作用 | `plan`、`ask`、`auto`、`elevated`、`full-local` 是本地访问级别；外部路径能力必须由策略明确决策
- `classify_action(request, workspace_root): ActionClassification`: 从工具名、递归路径参数和命令信号生成能力与风险提示 | 解析路径但不写入 | 路径执行 workspace containment；命令检查是保守启发式，不是 shell parser
- `PolicyConfig`、`ActionPolicy.evaluate(request): PolicyDecision`: 以不可变模式、网络开关和 workspace root 执行访问级别决策表 | 无副作用 | `ask` 只可确认外部读取，`elevated` 外部动作逐次确认，`full-local` 允许非 critical typed 外部动作；所有 execute 仍必须询问
- `sanitize_environment(host_env, allowed_names, explicit_env): dict`: 生成 Windows 子进程最小环境白名单 | 无副作用 | 名称不区分大小写，显式值仅限批准名称
- `redact_sensitive(value): value`: 递归复制并遮盖敏感键对应的值 | 无副作用 | 不修改输入
