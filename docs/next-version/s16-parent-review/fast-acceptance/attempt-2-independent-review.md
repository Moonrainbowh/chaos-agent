# 第二轮真实验收独审：FAIL

审查对象：`attempts/s16-source-completion-p4-beab64fc437a`。只读核对实际 wire、worker-result、durable、supervisor 与 stderr；未调用 Provider、未运行测试。沿用冻结 S16 来源调查质量门，不代表候选五平台发布验收。

## 已通过的边界

- 指定 `s16.sourceaudit` 成功委派一次，run `7c70bfa2767157959c315ba51c2b79bf`，child thread `720eef9cbbc14608848370a0d8227d5f`；仅四次完整 `read`、两次模型请求。request 4 中四个完整工具结果的 UTF-8 SHA256 分别与冻结版本及磁盘一致，物理行数为 7/4/2/9。未见禁止工具、第二子任务或文件修改；supervisor `candidate_unchanged=true`。
- 父 thread `310bbf7cf7224e98a67796c40b3eaf7c` 的 request 5/6 都是实际独立阶段请求，仅 system/user、完整四来源及物理行标识；无 advisory、child_objective、消息历史、notes、摘要。request 6 仅额外增加 `missing_delivery`，没有转入比较阶段。
- 六份 wire 文件 SHA256 均与 manifest 一致，均 `glm-5.3-flash / medium / 4096`。六条唯一 usage 全部 settled，同一父 owner；input **21455**、output **4700**、total **26155**。父来源四请求 **17609**（14111+3498），子来源两请求 **8546**（7344+1202）；子额度已包含在总数，不能再加一次。child receipt complete=true、8546 tokens、4 tools。
- 原父 1000000 token / 12 rounds / 40 tools，standard lease 12/30，无 renewal；最终共享 budget 为 6 rounds、10 tools、26155 tokens，active_seconds=114。原子额度 300000 tokens / 5 tools / 240s 未提升。终态保持 `unchanged/unverified`，不冒充已运行测试。

## 阻断与证据边界

1. **没有完成父复核交付。** 首次独立输出 `task_objective.citations=[]`，结构门拒绝，剩余为 `valid bounded source findings` 与 `review requirement: task_objective`。Host 只进行一次独立补全 request 6；终态 `failed / parent_review_delivery_unmet`，最终 remaining 为 valid findings、implementation、test_discrimination、task_objective。持久 parent_review 仅两条 independent 记录（准备、repair_used=true + rejected_initial），没有 comparison 或 delivered。不能将“两次独立请求”当作“两阶段完成”。
2. **首稿有可直接复核的语义错误。** `test_empty` 断言被描述为“cannot distinguish any wrong behavior since any list-returning stub passes”。返回 `[1]` 的列表 stub 对空输入仍会失败，故该全称命题错误；有限覆盖不能写成零辨别力。首稿“implementation coincidentally matches legacy only in that it does not sort”也错误：legacy 被否决提案是排序/去重，当前不排序并不符合该旧提案。首稿正确判定五条实现行为与五测试静态 pass/fail，不能抵消上述错误。
3. **子报告尚未被父比较纠正。** 子报告五条实现判断及三个静态失败方向正确，但 test_empty/trim/blank/duplicates 的引用全部比实际物理行偏一（应为4/5/6/7）；“duplicates 隐含顺序”不能排除交换两个相等值，“无混合用例所以无法区分先过滤再 trim”也过宽，现有 blank 用例就可暴露先过滤原始非空字符串后 trim、却保留新空串的错误。这些不是要求子报告预先完美：原门允许父比较识别并纠正子错误；本次尚无该交付证据。
4. **最后拒绝原文缺失。** request 6 实际发送、usage settled、Host 终态失败均有证据，但最终被拒绝的模型正文没有出现在 parent_review、用户消息或文本输出事件中。现有记录只能确认 Host 列出的 remaining，不能独立解释最后具体哪条引用、quote 或结构字段出错，也不能声称 request 6 已改正首稿语义。父 messages 最后一条仍为 delegate tool receipt，无最终可见 assistant 报告；旧 runner 的 `parent_delivery_observations` 因此 AssertionError。这是交付/观测缺口，不是 Provider 启动失败。

## 独立列出的 workflow 异常

`real-stderr.log` 记录后台 `WorkflowService.observe → _child → WorkflowNode.__post_init__` 的 `ValueError('title must be non-blank bounded text')`，title 校验上限512。后台异常未阻止真实 child 完成或父独立请求；不能归因为本次结构门失败。应单独定位 observation.title 的来源与长度，不通过放宽验收或重标 PASS 掩盖。

本次结论 **FAIL**。真实来源进入、授权与结算边界有通过物证；父两阶段、逐断言最终语义、精确物理引用纠错及最终可见交付尚未通过。两次真实尝试均保留，不再抽样。下一最小修正应先离线复现结构输出/引用拒绝与最后拒绝正文持久边界，保持原来源、预算及门槛。
