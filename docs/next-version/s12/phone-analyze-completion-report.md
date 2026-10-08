# S12 第四候选拒绝后完成：只读判据

唯一读取范围为显式 Temp `s12-phone-fdu276e7/state/sessions.sqlite3`，SQLite `mode=ro`，连接前 assert Temp/绝对路径/边界。没有读取用户 messages、Task objective、criterion description、设备凭据、回复内容。产物为 `phone_contract_facts.py` 与 `phone-contract-facts.json`。本次未改产品/fixture/live。

目标 Task `f40c6f8a3a7047fe927dd403549982f0` 的直接安全字段：status completed，冻结 intent analyze，interaction_mode ask；workspace_write/local_execute/network 均 false。完成契约 revision 1/intent analyze，1 个 `risk-appropriate-validation` 验收项，required/integrity；task_completions 无已验证最终落账。主 Agent 另提供实际 approval denied、无 mutation/evidence/.env 文件。

此终态符合现有契约：`core/engine_completion.py:_resolve_task_completion` 只有 MODIFY 且没有文件变化才强制 waiting_decision；`core/verification_state.py:decide_verification_transition` 对 ANALYZE 在无 VERIFIED 且 verifier NOT_RUN 时允许 completed，不把它升级为 verified。因而“执行结束、未验证”与“拒绝写文件后无待决定卡”可以并存；completed 不能当作写入成功或验收通过。

`interfaces/task_controller.py:resolved_task_mode` 在 auto 中将 `infer_task_intent(prompt, 'code') == analyze` 解析为 ask；`freeze_task_contract` 冻结 intent，`core/task_intent.py:infer_task_intent` 的 ask/plan 也明确选 ANALYZE。这里仅直接证明保存结果是 analyze/ask，没有阅读正文来推断用户本意，不能仅据此断言原 prompt 没有要求编辑。主 Agent 已检查 auto 小聊分支；相同自测明确修改 prompt 是更合适的 MODIFY 对照。

冻结权限 false 的准确影响：`policy/engine.py:_trusted_workspace_action` 用 allow_workspace_write 决定写操作能否成为预授权 trusted；False 不构成 Root 统一硬 DENY。ASK 模式仍可要求显式审批，`chaos_agent/permission_dispatch.py:authorize_action` 批准返回 None 让原 dispatcher 执行，拒绝返回 error_result。因此不能声称 ask Task 无论审批都写不了，也不能据此否定先前已批准手机任务的真实 mutation。此次 denied/no mutation 不需要另设权限假说。

`phone_fixture.py:ScriptModel.stream` 固定发 `.env` 工具，之后固定英文声明，既不按 prompt 规划，也不根据工具拒绝生成自然语言解释。这句话不是生产模型回复质量、意图识别或验证证据。固定英文本身不是 Task 卡丢失的证据。

明确对照 prompt 为 `Implement the fixed S12 offline fixture write only; no shell or other paths.`。原隔离自测证明该类 MODIFY 请求拒绝后 waiting_decision，并可 HTTP accept_partial；实际手机的新建会话对照由主 Agent 继续，不在本次只读调查中代替现场验收。
