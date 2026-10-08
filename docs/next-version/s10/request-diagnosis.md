# S10 最终请求预算只读诊断

结论：S7 的共享预留与用量投影已经到达默认主请求及辅助摘要，不应新建第二预算账本。但现有“最终请求”计数早于 Provider 实际序列化，存在可复现低估；默认 semantic 最后守卫也没有显式纳入 Host 20,000 总提示上限。附件在该守卫统一失败闭合，尚未形成附件可用的共同计数路径。

范围：只读审计主工作区代码，新增本报告和离线 probe，不修改产品、candidate、配置、数据库；没有真实 Provider/网络/部署/推送。解释基于 S9 后当前代码，不是已实现 S10 的验收。没有使用历史库或构造 Repository。

## 实际生产入口

- `chaos_agent/application_context.py:83` 的 `RuntimeContextFactory._budgeted_client` 为所有策略构造同一 `BudgetedWindowClient`。没有显式 context_policy 时采用 semantic；`_strategy_context:141` 将该 guarded client 同时交给主 Engine 和 `ModelSemanticSummarizer`。
- 默认 semantic：ThreadAwareContextBuilder → WorkspaceContextBuilder → BoundSkillContextBuilder → ContextAssembly；线程/工作区包装在同一 assembly 中保留 guarded client。`_profile_prompt_budget:178` 常规上限 20,000，规则 6,000，消息 12,000；profile 仅收缩该 builder 上限。`_semantic_limits:220` 为旧语义摘要计算自己的历史/输出候选额度。
- 显式 summary/boundary：`build_managed_context` → WindowContextBuilder；HandoffWriter 对同一 guarded client 使用 `stream_for('handoff',...)`。有 builder/Handoff 的预检查以及 client 的再次检查，但这些是容量检查，不重复预留。
- 显式 persistent：PersistentContextBuilder，无模型交接摘要；注入 GUIDANCE、来源 ID、剩余额度提示及显式记忆引用后计数。仍使用同一 guarded client。
- `context_windows/client.py:21–44` 在逻辑输入计数后，调用一次 `reserve_context_call`，预留 estimate + 最大输出 + safety；usage 存在时经 `settle_context_call` 唯一投影。`accounts_task_usage=True` 禁止 Core/语义摘要再次 consume。缺失 usage 保留负债，不被误记为精确零。

## 已证实缺口

1. **最终序列化低估。** `PromptTokenCounter.request` 使用 `ToolCall.to_dict()` 的 `ensure_ascii=False` JSON；Chat `openai_chat.py:53`、Responses `openai_responses.py:49` 将参数变成默认 `ensure_ascii=True` 的 JSON 字符串。这是解码 HTTP JSON 后仍存在的 function.arguments 字符串差异，不能用“传输 escaping 不算 token”排除。工具自身又经 Chat type/function、Responses type、Anthropic input_schema 等映射；counter 并未读这些最终 schema。
2. **最后容量来源分裂。** 无显式 policy 时 client cap = profile combined − profile output − safety，并可被 profile api_input 限制；它没有 `PromptBudget.max_prompt_tokens`。Workspace 使用 ASCII/4、多字节/3 的本地估计，client 常用 o200k tokenizer 或逐 UTF-8 字节估计，两者不同。因此 builder 的 20k 通过不能证明最后请求符合 20k。辅助语义源同样以旧估计和独立 max_total_tokens 裁剪；client 未接到该次摘要更小的允许额度。这里是代码事实，尚未构造完整 Application 超 20k 的反例，不虚报已跑此验收。
3. **附件可用性未闭环。** `counting.py:16` 对任意 attachment 直接 ValueError。ProviderAttachmentEncoder 已能完整性复核 text/plain/image/png 并产生原生块，但默认 guard 在此前拒绝，错误仍写成 managed windows，即使当前是默认 semantic。Context 的尺寸/文本估计与 Provider 的实际 wrapper/解析内容不是同一计数路径。图像原生 token 只能采用明确保守政策，不可把 base64 HTTP 字节声称为 Provider 实际 token。
4. **网络前还有变换。** Chat `_sanitize_tool_pairs:75` 会补 interrupted tool outputs；应遵守 Core 未决动作恢复门，预算层不能再补假执行事实。Transport `stream_sse:85–99` 在 authenticated_request 后以 `json=dict(payload)` 发出；Codex auth `auth_request.py:47` 删除 max_output_tokens，虽是既有协议兼容，但不能把 client reserve 上限宣称为远端输出被强制限制。Antigravity 信封、Google/Pi 原生 payload 同样在逻辑 counter 之后生成。不得因 S10 改动覆盖用户 authentication 文件。
5. **计数展示不等于请求账本。** managed builders 同时保存旧 prompt_estimated_tokens 与窗口 prompt_tokens，persistent 还加入固定 160 提示预留；不同估计有契约解释。应统一用于发送/预留的核算对象，保留展示/策略数据兼容，不能把多个检查误当多次收费。

## 离线物证

命令：candidate locked CPython 3.13.2 执行主工作区 `docs/next-version/s10/request-diagnosis-probe.py`；脚本显式把主 src/root 放入 sys.path，Provider 采用 httpx.MockTransport，没有 SQLite、真实 API 或读取凭据。

probe 是闭合 assistant/tool 组，实际 OpenAIChatClient → ProviderTransport → HTTP serializer。结果保存在 `request-diagnosis-probe.json`：o200k logical estimate **3,355**，effective input cap **3,356**；HTTP JSON 解码后实际 function.arguments 字符串单独已 **15,005** tokenizer token，却已经发送 **1** 个 mock HTTP 请求。共享预留 **1**、结算 **1**，输出配置实际为 **128**。该反例只证明 serializer/counter 错位，不是 Provider 实测用量或真实模型质量证据。

## 最小共享核算方案（待实现授权）

1. 在 Providers 暴露明确、不可变的 prepared request / request estimate 接口，以实际协议消息、工具 schema、附件块及冻结输出配置生成一次核算对象。Guard 使用该对象计数、检查和 S7 reserve 后发送同一对象；避免先 count A 后 send B。不得读取 wrapper 私有属性或从 model 名猜协议。
2. 一个共同请求额度对象表达 Host 总提示、API combined/input、最大输出、协议与 safety 预留。最后 cap 取实际适用上限最小值；builder 只负责在这个额度内选材。输出预留和 safety 不重复从 already-effective cap 扣除。辅助 summarizer/handoff 可以收缩同一个公共请求额度，不扩大 profile/frozen task。
3. 保留现有 Sessions 原子 reserve/settle，新增 prepared metadata 仅用于同一个 admission，不 consume 第二次。没有用量继续保留原负债语义，部分用量是下界。请求字段/认证变换使输出上限不可保证时明确能力诊断，不能补造 token 实测。
4. text/plain 可按已解析、完整性复核后的真实不可信文本块计数；图片需要明确保守 profile 政策，否则发送前明确拒绝并解释，不静默零计数或沿用未经校准的尺寸公式。纯文本先完成 shared accounting，不迫使所有 provider 立即支持同一模态。
5. fake model 可使用明确公共逻辑 prepared 兼容实现；真实 Provider adapter 必须实现真实 serializer 核算。Chat/Responses Unicode 参数、全部工具 schema、附件/无能力、默认 semantic+summary/boundary/persistent 主/辅助、超限零 HTTP、共享计费一次、重试同一 payload 均纳入确定性测试。全过程保留完整 assistant/tool 组和原始日志。

建议继续保留已验收 semantic 为默认。此诊断没有比较模型效果，不能据此宣布 persistent 或 boundary 更优。历史有界查询由 S10 相邻实施范围负责；本报告不实现 S11。

## 冻结任务的策略身份补充

Root 提醒后只读核验：`runtime_provider_controls.py:235` 恢复时从当前 `_profiles` 重新 freeze，`:283` 的 `_profile_identity` 只匹配 model/protocol/endpoint_host；TaskContract 未记录 context_policy/context_window/api_input_tokens。这样同名 profile 的窗口配置变化可越过原运行身份检查，不能把当前配置当作旧 Task 的冻结上下文策略。

最小方案：给新任务持久化版本化的 context selection facts（strategy，包括显式 semantic；完整已校验 policy 参数；combined/input/output；最终 Host prompt cap 与 safety；可确定的 counter/协议政策版本），并将该身份纳入新的 runtime digest。恢复依据记录 facts 重建本次 Task 的 immutable profile/context 参数，不用当前同名 profile 覆盖；凭据仍从现行认证来源解析，不能把凭据持久化到该快照。已保存窗口有 strategy/配置标记时核对来源，一致才能重建。

旧 Task 没有 facts 时不得从今天的 metadata 推断过去已经是 persistent。明确使用旧 semantic 兼容路径或由已持久化的窗口记录证明旧 managed 策略；记录不足或冲突进入等待决策，保留原日志。不能为了迁移直接重算旧 digest 或删除窗口记录。Task 恢复、同名 profile policy/cap 变化、旧 semantic、已有 managed windows 重启都需临时库反例。

配置默认入口保持：没有显式 policy 且 metadata 未 enabled → semantic；显式 policy → 对应 managed；`config/context_policy.py:21` 的 local model_metadata.token_budget.enabled → persistent 属现有兼容入口，不更改用户配置，不改写已有 Task。
