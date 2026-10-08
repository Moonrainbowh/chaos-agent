# S9 进展与收敛只读诊断

当前存在两个相反问题：无正文的有效调查会被五轮形状规则提前收尾；正文、换窗、新失败和无关新读取又能被当成进展。修复应沿用 `TaskState`、`ToolOnlyConvergenceGuard`、`TaskProgressSnapshot`，以同一组 Host 事实区分有限候选读取与可续租进展，不增设第二套执行或进展框架。

## 当前代码与实际夹具

- `core/exploration_repeat.py::ToolOnlyConvergenceGuard.observe` 在 `has_text`、无调用或任一工具名属于 progress 时清零；没有读取结果是否成功/是否改变的输入。`new_context` 属于 progress；失败的 write/verification 仅凭名字也能清零。
- `_engine_convergence.py` 传模型正文与调用形状，未传实际完成事实。
- `_engine_run.py::_task_progress_snapshot` 取最新 tool 消息；原始工具名白名单与真实 action identity 不一致，读取输出与参数变化均进入 digest，未检查相关性。`failure_fingerprint` 的新失败也使 digest 改变。
- `_message_fingerprint` 忽略 call ID，却未排除结果中的 duration/request ID 等易变字段。最新 A/B 读取可轮流产生不同 digest；其本身不能证明比上个租约多掌握了信息。
- `sessions/_task_budget.py` 先检查硬预算，再在软额度越界时比较 snapshot digest；续租最多三次。保留这个顺序和上限。真实新失败属于诊断事实，不能充当正向续租事实。
- `_engine_dispatch.py` 已能得到 Host 解析的 call 与实际 `ActionResult`，并在落地 tool 消息后观察 exact repeat；这是接入事实的自然位置。逻辑修改 commit 在批次末进行，generation 必须在 commit 之后比较，不能在首个 ACTION_COMPLETED 时提前猜测。
- `TaskState.verified_facts` 还接受 `TaskStateUpdate`，不是所有条目都构成可信证明；不可简单哈希这个自由文本列表当进展。

2026-10-04 使用锁定 CPython 3.13.2、主工作区源码运行 `progress-diagnosis-fixture.py`，退出 0，得到：

```text
distinct_successful_read_shape [None, None, 'warn', None, 'finalize']
opaque_with_repeated_text_count 0
unproved_context_reset_count 0
unchanged_content_different_duration_changes_digest True
new_failure_changes_digest True new validation failure
```

这是直接调用现有守卫与真实 snapshot 方法的隔离夹具；不是生产任务已通过的证据。最终验收必须经过下文的默认应用装配。

## 最小判定与信任边界

一个 Host 事实观察结果，包含 `candidate_novelty` 与 `renewal_progress` 两个等级；二者都来自已配对落地的成功结果和 Host TaskState/验证账本，而非模型文本。这不是两套独立判定：同一函数返回较弱的探索候选和较强的额度续租事实，guard 与 budget 各使用对应等级。

1. 新读取候选：仅确定性解析为 read 的成功结果，具有非空实际内容、非空搜索匹配或新增真实文件条目。去掉协议 ID/耗时后，对规范化资源及实际内容做指纹。相同路径不同有效切片可为新信息；仅调用 ID、换名、页码而返回同一内容不是新信息。空搜索、失败/opaque/契约加载、上下文切换均不是读取进展。
2. 读取相关性：只用冻结目标明确提到的规范化文件/目录，以及目标明确全仓库调查的 root。随后允许由相关 search 的真实匹配路径形成直接读取链。搜索 query 需要与冻结目标中的明确标识一致；不能只因模型自选 query 或在参数中塞入目标词就接受。路径前缀按路径段匹配，拒绝 `src/auth-other` 命中 `src/auth`。已改变的实际 subject 文件可作为后续读取/验证关联对象。
3. 无明确路径的自然语言任务：优先目标中的明确 symbol/query→成功搜索实际匹配→读取对应文件。不要使用通用停用词、整句相似度或模型宣称相关。无法确定关联的读只是有限探索候选，不能无限续租。对于确有必要的首轮目录发现，可给出一次有界候选探索窗口；这必须明确标为 bootstrap，而不是伪造“相关已证实”。不建议为了此项新增表/持久策略配置；现有初始软租约已提供 bootstrap，应先验证它是否足够。
4. 真修改：使用已提交逻辑修改后的 generation/subject。只调用 write、write 返回 unchanged、被策略拒绝、模型说已修改均不算。实际部分修改仍使 subject 变化并失效旧证据，但结果保持失败/未验证，不能冒充完成。
5. 新验证：只能使用 trusted verification 的 subject generation/hash、recipe/criterion 和稳定结果内容。相同对象反复同样 PASS 或 FAIL 不是新信息；普通 command exit 0 不是验证。首次新的失败验证可用于诊断/反馈，不应仅凭 failed fingerprint 续租；既有失败在同一相关验证对象上变为 PASS 才是解决失败。
6. 解决失败：原失败的规范化操作签名在实际执行后成功，或对应验证失败在同对象复验通过；成功读取其他文件、换失败签名、`last_failed_call=None` 不足以证明原问题已解决。保留失败事实给反馈，但从正向 digest 移除“新失败”。
7. 用户 steering 是新授权需求事实，可以重启局部停滞观察，但不增加硬预算；仅 assistant/developer notices 或换窗不等价。

读取去重集合应从当前完整持久历史确定性重建，再计算累计稳定摘要，不能只比较 latest tool，也不能由有界集合淘汰 A 后又把 A 当新信息。可选择“最多固定数量的新颖指纹被接受，达到上限只停止新增”，保留固定内存边界；已被截断的事实不自动再次新颖。当前完整 history 读取为既有事实，S10 才做有界查询；S9 不新增存储表。

候选读可让无正文的多步正常调查免遭形状规则误停，但应有显式总量上限并仍受初始/软/硬额度。只有相关的新信息、真实 subject 变化、可信新验证/失败解决进入累计续租摘要。无关新文件不能无限延长，正文不能重置，unknown/失败不能产生正向事实。

## 接入与反馈

建议修改：

- `src/code_agent/core/exploration_repeat.py`：守卫接收 Host 事实；保留参数纠错计数和 exact repeat；移除 has_text/new_context/调用名清零。维持 warn→具体可恢复提示→有证据的 summary/pause 路径，已有正文只影响是否还需总结。
- `src/code_agent/core/_engine_convergence.py`：转交批次已提交后的事实，不使用模型正文当进展。
- `src/code_agent/core/_engine_dispatch.py`：复用 resolved call、配对 ActionResult；失败/opaque不算；批次真实 generation 变化在 commit 后统一观察，避免验证失败与读取guard互相矛盾地发终态。
- `src/code_agent/core/_engine_run.py`：从相同观察结果构造累计 snapshot；成功读取根据 canonical Host identity，而不是日志 raw name；去掉新失败的正向摘要。
- `src/code_agent/core/limits.py`：必要时调整 snapshot digest 语义，保留旧预算读取兼容和硬上限，不改变默认额度/续租次数。
- `src/code_agent/core/AGENTS.md`：同步新进展约定，移除旧“无正文/换窗进展”条款矛盾。
- 相关测试：`core/tests/test_exploration_repeat.py`、`test_engine_budget_lease.py`、`test_budget_lease.py`、生产 `tests/` 新固定轨迹；必要时只调整 sessions budget 夹具期望。无证据不需要改 TaskState schema。

不建议把 tool 输出任意 metadata 中 `progress=true`、模型写入 TaskState.verified_facts 或任意名称 `run_verification` 当可信来源。typed verification ownership 和 subject 有效性应复用现有验证服务/账本，而非重造证据格式。

## 默认应用装配的验收轨迹

用 `tests.agent_app_test_support._isolated_application` 创建真实 Application/SQLite/Workspace/Foreground，只替换 factory 的 Provider client 为固定脚本模型；保留真实 ContextAssembly、TaskScoped Dispatcher、权限、Core 与 budget。创建实际源文件；通过 `app.tasks.start` 和 events 执行，不直接构造替代 engine 或低层 controller。每个轨迹断言工具实际次数、TaskRecord/TaskResult、预算累计、消息配对及工作区内容。

| 轨迹 | 预期 |
| --- | --- |
| 明确目录调查，连续八个相关不同文件，不输出正文 | 不在第5轮强制停止；可续租，最终真实总结 |
| 目标关联 search 返回真实路径，逐个读取不同切片 | 新内容候选/相关信息；别名与默认 compact 工具结果一致 |
| 12轮“继续查看”+同结果/opaque | 不因正文清零；有界警告/暂停；硬预算不突破 |
| A/B重复内容、随机duration/IDs、递增无关文件 | 不续租；exact repeat 或软租约/停滞路径有界终止 |
| 无关 failed read/write/new_context 调用 | 不进展，原失败反馈保留 |
| 不同文件实际 edit，无正文 | generation 推进，不按形状收尾；旧验证证据失效 |
| 同一subject相同PASS、普通exit0、首次换失败签名 | 不自动证明完成，也不反复续租 |
| 真实失败后同签名成功/相关可信复验PASS | 解决失败构成进展；结果不丢历史失败事实 |
| 预算最后额度、重启恢复、用户steering | 持久累计不重置；硬预算先于续租，steering无扩额 |

无需修改、未验证、环境受阻、失败、通过的交付状态继续由 S6 TaskResult 与最新 completion assessment 判定。读得新不等于验证通过，达到守卫也不能强制伪完成。最终独立监督应运行上述正反轨迹，不以提高阈值或停用守卫代替修复。
