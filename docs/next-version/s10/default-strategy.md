# S10 默认与兼容决定

推荐默认继续是 semantic。未提供 context_policy 时 RuntimeContextFactory._strategy_context → build_context_runtime → ThreadAwareContextBuilder → ContextAssembly；摘要只作辅助、原始来源与完整工具组保留。没有证据证明其他路线在当前目标上更优，本阶段不替换默认。

| 入口 | 路线与边界 |
| --- | --- |
| 无显式 context_policy，local model token_budget 未启用 | semantic 推荐默认 |
| 显式 context_policy strategy=summary 或 boundary | WindowContextBuilder 兼容/实验；保留 HandoffWriter 与既有摘要/边界语义 |
| 显式 strategy=persistent | PersistentContextBuilder 兼容/实验；保留 history/notes 工具与原 journal |
| local model_metadata.token_budget.enabled=True，未另配 context_policy | 原有 persistent 选择保留，属于显式配置兼容；不推广为第二推荐默认 |

所有路线通过同一 BudgetedWindowClient 处理真实 Provider prepared bytes、S7 reservation/settlement 与 Host ceiling，不各自创建模型执行循环。未知模型的 UTF-8 byte 上限估计与已配置 tokenizer 估计是本地计数，不能冒称 Provider 实际 usage。图像未提供明确校准政策时，最终门在 HTTP 前拒绝；文本附件准备时验证完整性并计入实际序列化内容。

新 Host Task 持久保存白名单 context_selection：策略/版本、policy、context/input/output、Host prompt/safety 与 counter version。恢复先检查该快照，再检查已有 runtime identity；漂移进入现有等待决策状态，不写配置、不重建推测的旧 profile。旧 4/8 profile facts 与无 context_selection 合同可沿既有配置兼容恢复，但无法事后证明其历史上下文选择；保持缺失事实，不补造新快照。没有删除策略实现或用户记录。

bounded materialization 保留 checkpoint/window 覆盖前缀与未覆盖 suffix、最新真实 user 及完整 assistant/tool 组。无 checkpoint 且超过明确读取限额的旧日志需要显式压缩/恢复选择，不能悄截尾声称已覆盖。冷热基准必须区分首次逐页来源验证与缓存后读取；两者都限制单页与累计 materialization 内存。
