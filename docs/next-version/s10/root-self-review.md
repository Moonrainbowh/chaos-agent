# S10 主 Agent 集成核验（不替代独立监督）

已接 Host PromptBudget 与语义摘要的辅助限制，TaskResult 使用生产仓储身份投影。20 项 Host/默认 semantic/persistent/持久结果集成测试通过（host-integration-tests.log，2.909s）；另 7 项策略冻结/Host cap/有界投影测试通过（root-focused-tests.log，3.979s）。均用候选的锁定 CPython 3.13.2 执行主工作区源码，不属于最终候选全量。

审查发现并反馈实施者：

- result 与所有 RUNNING 事件合并后 LIMIT 32 能互相挤掉；改为分别取最新 RUNNING 和同代际/subject 的 result，再按原顺序合并，取消 owner 检查保持。
- 摘要 max_output_tokens 小于真实 Provider 输出预留时，仅减摘要名义输出不足；auxiliary_total_tokens 在准备请求后按真实输出及 safety 收缩。
- checkpoint/window 来源 hash 正确仍可能跨未闭合组边界；要求流式验证完整 assistant/tool 分组，再允许缓存来源有效。
- 热进展缓存无新增记录时直接返回，可能遗漏并发同序号修改；要求返回前重新检查 revision/epoch。
- 仅 rows 上限不足以限制巨大 arguments、summary/journal payload；要求 SQL 字节预检先于 Python materialization。

以上问题已修复并经针对性反例复核，详见下方补充及独立监督记录。最终物理快照、标准全量与独立监督后才放行 S10；S11 尚未实现。S9 候选基线 31 路径物理哈希核对通过，临时 Git index 写出 accepted tree 1161bad09f69f1517df6e55763f6141fdeb5178b；真实 Git index 未使用。未访问默认用户历史库、未升级 live Host、未提交或推送。

当前回归补充：默认历史恢复/旧终态投影/用量 23 项 PASS；RuleBudget、ACP、终端状态、pending action 集成 52 项 PASS；冷 `/compact` 线程绑定与异常恢复、TUI 真实迁移回执及 cost 兼容 10 项 PASS（root-final-entry-tests.log）。上述早期反馈均已由实现与独立反例修复验证；最后冻结候选的全量和正式监督仍待执行。`/cost` 生产路径已改用 SQL 汇总，并经独立禁止全量日志读取的反例通过。

冷 `/compact` 必须绑定调用线程后才使用共享预算守卫；ContextAssembly 使用 scoped bind/reset，异常亦还原外层线程。长历史迁移的候选容量检查只作选择，发送仍准备当前实际请求、重新检查并登记同一任务预算，不能把预检当授权。


四策略旧长日志迁移在当前 frozen 方案内增加显式退出路径，不静默迁移新策略。consumer 的 6000 行三窗口恢复 focused 通过（78.829s）；semantic 46项通过。独立冷 RuntimeContextFactory→ContextAssembly.compact_context 实际预算收费一次通过（supervision-cold-compact-corrected.log），并在异常/结束后恢复 binding。persistent Host1000连固定指引都不容纳，该负向不通过增加生产总提示额度解决；正向采用明确Host2000<work6000，boundary合法八字段输出fixture128不足改512，日志保留原失败。正式窗口套件与最终候选全量尚待结束，不能用 focused 代替。
