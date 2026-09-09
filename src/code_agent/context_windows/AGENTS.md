# Context Windows
在同一个持久任务内按 API 能力和可配置策略切换上下文，保留可追溯历史并独立核算任务消耗。

## 边界
- 新增 `persistent`：本地 History/Notes 对齐 Codex 主动换窗语义；不生成或携带模型交接摘要，提示剩余额度、稳定窗口/条目引用，接近容量提醒保存笔记，硬上限兜底换窗。
- `persistent` 在同一任务内读取原始持久消息（含工具参数/结果）、按路径维护笔记；保留最新真实用户请求。笔记不自动全部注入，新窗口提示按需恢复；不开放跨任务读取，不调用 Codex 私有后端。
- 旧 `summary`/`boundary` 及其冻结评测保持语义；本次不修改私人配置或推广为全局默认。有效容量不足以承载单条必要用户消息时明确失败。
- 负责：输入/输出/合并容量校验、最终请求计数、准备/切换阈值、普通摘要和显式交接两种策略。
- 负责：在完整工具调用组之间切换；原始消息不可被摘要覆盖；交接和笔记均作为待核实历史，不升级为系统指令。
- 负责：通过 Sessions 的原子日志保存窗口、笔记、请求与用量；重启恢复、幂等请求和并发冲突检测。
- 负责：当前任务范围内的有界历史检索和笔记工具；换窗请求在工具结果落盘后的下一轮生效。
- 负责：每次主请求和辅助摘要均执行硬上限和独立任务预算检查；缺失用量或容量不能声称已精确核算。
- 初始候选：256000 输入工作窗，75% 准备、87.5% 切换、16000 安全余量，独立任务上限 5000000；配置可降低，上限按已知 API 限制计算。
- 不负责：自动升级模型、扩大工作窗、修改用户私人配置、自动推广实验策略或创建新用户任务。
- 不负责：把估算 token 当作 provider 实测，或把机制回放当作真实模型效果。

## Units
- `PersistentContextBuilder`: 剩余额度提示、主动/硬容量换窗及恢复引用 | Sessions I/O | 不调用 HandoffWriter、不注入整份笔记，完整工具组落盘后才切换
- `PersistentToolService`、`history_action`、`notes_action`: 稳定窗口/条目定位、含工具参数的原文检索、按路径笔记及有界分页 | Sessions I/O | 当前任务隔离；工具名用下划线适配现有 API，原文保持不变
- `WindowPolicy`、`ApiContextLimits`: 明确容量来源、软阈值和独立任务上限 | 无副作用 | 输出预留按实际 client 配置
- `PromptTokenCounter`: 对最终系统提示、消息、工具协议统一估算 | 可选 tokenizer | provider usage 为实测；未校准模态拒绝
- `BudgetedWindowClient`: 每次主/辅助调用前原子预留、调用后核算 | Provider 和 Sessions I/O | 中断无用量保留预留，不重复扣缓存 token
- `closed_group_ends`、`select_window`、`carried_messages`: 保持工具组、原始用户请求和来源锚点 | 无副作用 | 过期锚点拒绝恢复
- `HandoffWriter`: 等源范围、等输出额度的自由摘要/显式字段交接 | 有界模型调用 | 失败保留原历史
- `WindowContextBuilder`: 完整前缀构造一次后选择持久原文、检查阈值、原子提交新窗口 | Sessions I/O | 不调用旧的二次历史裁剪
- `WindowToolService`、`context_tools`: 当前任务的历史检索、工作笔记与延后换窗请求 | Sessions I/O | 不接受模型指定跨任务 ID

- `QueuedContextBoundary`: 手动请求的排队回执 | 无副作用 | 不冒充已经完成的压缩检查点

- 仅计数契约：窗口组装后刷新 `prompt_estimated_tokens`（复用 Context 本地估计器）、`prompt_budget_tokens`（有效 input cap）与 `prompt_safety_tokens=0`（cap 已处理预留，不重复扣减）。原 `prompt_tokens` 继续使用窗口专用 counter，控制策略不变；两种本地估计可能不同，均不代表 API usage。
