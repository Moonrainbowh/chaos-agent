# S16 快速复验结果：未通过

2026-10-08；遵照本轮要求，没有运行全量测试。候选为 `codex/s16-parent-review` 工作树中 `b28868a` 加未提交补丁，每次运行的 `candidate-manifest.json` 保存 HEAD、源码哈希、dirty diff 和 runner 哈希。不是干净提交或新五平台 CI 验收。

**S16 真实来源调查及父复核质量门仍为 NOT_ACCEPTED。** 本轮修复了工具披露缺少委派选择器互斥说明的问题，也证实父复核交付门会拦截不合格输出；未获得合格的独立初判、比较阶段及最终交付。不能以定向测试通过或子任务完成替代验收。

## 本轮改动和定向验证

- `src/code_agent/core/parent_review.py`：要求按具体输入、断言和静态结果判断测试能力，给出可检查的检测/遗漏见证，限定结论范围，比较时引用实际子断言。没有写入 S16 答案或改变交付门。
- `chaos_agent/tools.py`：在工具及参数说明中明确 `role` 与 `agent_id` 必须二选一，自定义 Agent 仅填 `agent_id`。实际运行时互斥校验、权限、模型、预算和 fixture 均未修改。
- Core 与实际请求装配 **9 项通过 / 8.040s**，见 `targeted-tests.log`。选择器与生产交付 **2 项通过 / 7.651s**，见 `selector-targeted-tests.log`；后者首次新增 wire 断言误用另一协议的字段形状，修正测试后通过，初次错误日志保留。这两组覆盖 11 个不同测试，不是全量结果。
- 薄 runner 初次启动捕获接口错误发生于 Provider 请求之前；真实应用创建、父子路由读取和关闭落盘离线检查通过后修复。原错误及零 wire 物证见 `attempts/s16-source-completion-p4-f65a2823f406`，说明见 `startup-repair.md`。

## 真实结果

| 运行 | 实际调用与用量 | 判定与物证 |
|---|---|---|
| 两次局部推理探针 | 2 请求，2,760 tokens，45.3s | 12 例输出/状态/身份共 36 字段正确；测试能力概括仍错误，`PARTIAL_WITH_ERRORS`。见 `semantic-review.md`、`review-results.json`、`script-provenance.json`。 |
| 整链第一次 `fa5e01b912a3` | 7 个父请求，43,096 输入 + 1,944 输出 = 45,040 tokens；任务约 71.0s | 第一次 delegate 同时填 role/agent_id 被拒，第二次选了非指定角色被冻结检查拒绝；没有真实 child 或父复核阶段。父 fallback 错称 duplicates 测试通过、empty 无区分力。见 `attempt-1-independent-review.md`。 |
| 整链第二次 `beab64fc437a` | 6 请求：父 4 + 子 2；21,455 输入 + 4,700 输出 = 26,155 tokens；任务约 116.6s | 指定子任务读取四源并交付；父独立阶段初稿及一次补全都未满足交付检查，终态 `failed / unchanged / unverified`、`parent_review_delivery_unmet`，没有进入比较或最终交付。 |

真实调用总计 **15 请求 / 73,955 tokens**，包含失败和局部探针。两次整链均 GLM-5.3-flash / medium / 输出 4096，原来源字节、父子额度和时限保持；不是更换模型或追加预算后的结果。第二轮子用量 8,546 已包含在共享父账本中，不重复相加。

## 第二轮还卡在哪里

1. **语义错误仍存在。** 已持久保存的独立初稿称空输入断言不能区分错误行为、任何返回列表的 stub 都通过；这是错误概括，返回非空列表即可使该断言失败。该初稿未见子报告，因此不能将错误归咎于抄袭子报告。五项基本实现行为判断正确，并不抵消测试能力判断错误。
2. **模型没有可靠遵守引用交付契约。** 初稿 `task_objective` 的 `citations` 为空；一次补全后仍被判定为不合法 findings，涉及 implementation、test_discrimination、task_objective。交付门按原规则阻止完成，未放宽、未追加补全。
3. **诊断物证有缺口。** 初稿拒绝文本已保存，但最后一次被拒回复没有存入复核 checkpoint，普通事件也隐藏了该内部输出。因此只能确认最后一次检查结果，不能逐字断言其具体引用错误。这是后续应修正的失败输出留存问题，不把推测写为物证。
4. **另有工作流投影异常。** stderr 出现 `WorkflowService.observe` 的 `title must be non-blank bounded text`。子任务及父独立阶段仍实际运行，不能据此解释模型语义错误；单独保留为未修复缺陷。

第二轮原始物证位于 `attempts/s16-source-completion-p4-beab64fc437a`：`worker-result.json`、六份 `real-wire-request-*.json`、父子 `durable-*.json`、`real-supervisor.json` 和 stderr。[第二轮独立审查](attempt-2-independent-review.md)及该目录 `checks.json` 确认 FAIL，并核对实际来源、隔离、哈希与唯一结算。旧 runner 最后还因“必须有父最终答复”的断言退出 1，这是未交付后的验收断言，不是导致父交付门失败的起因。

## 判定和后续边界

本轮停止于计划的两次整链尝试，没有通过重复抽样、改分或放宽引用来凑 PASS。下一步最小可审查工作是保留每次被拒输出并返回具体字段/引用错误，随后再评估语义复核能力；单纯继续增加同条件请求目前没有通过依据。

所有历史失败、旧 unknown 和旧候选验收范围保持。代码仍在独立工作树，未提交、推送或合并；本轮没有运行全量、跨平台 CI 或 UI 验收。
