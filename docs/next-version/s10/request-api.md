# S10 请求核算公共接口（实施约定）

- 真实 Provider 客户端新增 `async prepare_request(system, messages, tools) -> PreparedProviderRequest`，以及 `stream_prepared(request)`。准备完成协议、附件完整性校验和认证 payload 变换；对象冻结最终 HTTP JSON bytes，headers/credential 不参与计数、不入 repr。所有协议发送该同一份 bytes；重试也重用它。
- `PreparedProviderRequest` 公共 `input_text` 为最终 JSON 文本，`max_output_tokens` 为本地冻结输出预留，`output_limit_enforced`/`diagnostics` 明确远端字段是否存在；`uncalibrated_images` 标记尚无明确图像政策。JSON 文本估计包含 schema/参数字符串/协议外壳，是本地保守估计，非 Provider usage。
- `RequestBudgetConstraints(host_prompt_tokens=None, auxiliary_input_tokens=None, counter_version='prepared-json-v1')` 放在 context_windows.policy，供 Root Task 冻结使用。`BudgetedWindowClient(..., constraints=None)` 和 `stream_for(..., constraints=None)` 接受它。每次请求 cap 为 API/work cap、Host cap minus safety、aux input cap 的最小值；output 按真实 prepared max_output_tokens 与已配置 limits.output_tokens 的较大者预留。已扣 safety 的 effective cap 不再重复扣。
- Root 初次装配把 `_profile_prompt_budget(...).max_prompt_tokens` 传入 `host_prompt_tokens`；辅助摘要可 `stream_for('semantic-summary', ..., constraints=RequestBudgetConstraints(auxiliary_input_tokens=...))` 收缩额度。调用约束只进一步收缩，不能覆盖扩大构造时 frozen 额度。需要复用包装时通过公开 `.constraints` 校验，不查私有字段。
- fake model 缺 prepare/stream_prepared 公共方法时使用清晰标识 logical compatibility estimate；真实 adapter 均提供两方法，缺一拒绝，不能悄悄退回旧计数。S7 reserve/settle 路径及未知/部分用量不重写。
- 图像缺校准政策时 Guard 在 HTTP 前明确拒绝；既有 Provider 直接 stream 的图片映射契约继续保留。text/plain 在 prepare 时解析、验 hash 并编码，计数实际文本块。

追加辅助总额：RequestBudgetConstraints 同时支持 `auxiliary_total_tokens`，input_cap(api_cap,safety_tokens,output_tokens=0) 用实际 prepared/profile 输出较大预留收缩为 auxiliary_total_tokens-output_tokens-safety_tokens。摘要仅传按旧 request.max_output_tokens 计算的 input cap 不足以证明总额，Root 同时传总额。

追加冻结接线：TaskContract.context_selection 保存严格白名单 immutable JSON mapping；ProviderControls.profile_facts 新增第 9 项 canonical JSON，freeze_task_contract 保留旧 4/8 项兼容。新 Host 任务快照记录实际 strategy/policy/window/input/output/Host cap/safety/counter version。恢复在 active_matches 前核对当前公开 profile/预算快照，漂移明确拒绝，由现有等待决策处理。不引入重建旧 profile 的新 manager；旧无 snapshot 保留已配置兼容路径，无法事后证明旧策略，不补事实、不写配置。
