# 子任务本地预算拒绝独立诊断

结论：**CONFIRMED_FIXTURE_BUDGET_MISMATCH**。这解释 v2 子任务为何在首次 HTTP 前失败，不把 v2 或 S16 改判 PASS。

独立复跑 `diagnose_child_admission.py`，实际 exit0，生成独立 owned case `child-admission-offline-faffcb25525a/diagnosis.json`。原生产 Host、声明式 medium/只读 Agent、真实 prepared request 计算与 reserve_context_call 未替换；观察器调用原方法并原样返回，HTTP send 被禁止并计数。实际 HTTP attempts **0**。

独立复现 estimate **13311** + output reserve **4096** + safety **16000** = reservation **33407**，超过冻结 child ceiling **30000**。实际异常精确为 `EngineLimitError: child task token budget has insufficient remaining capacity`，advisory failed/unknown、0token/0tool/usage.complete=false，与 v2 首次 child model_started 后 engine_limit 相符。

源码 `context_windows/client.py` 先计算 prepared 请求，再将输入+输出+safety 全额向 `reserve_context_call` 申请；`sessions/_context_journal.py` 先检查父累计总额，再以 child max_total_tokens 检查子额度，成功后才插入 usage 和进入真实 stream。拒绝发生在 dispatch/HTTP 前，属于正确守门；300k Host 输入上限、20k工具提示额度和单个child30k累计限额是不同约束，不能因输入在300k内便宣称child30k可容纳。

这是验收 fixture 局部限额误配，现有证据不要求产品改代码、降低safety或放宽守门。新 child-only v3 的60k只能作为另一个明确冻结样例，在用户授权后执行；不是修改原失败。即使60k首请求可准入，仍不保证后续累计请求和最终源质量通过。父 v2 pending80703未知负债及两次真实失败继续保留，不以HTTP0诊断替代结算或收费结果。

独立日志为 `child-admission-independent.log/.exit`。此次没有产品/candidate修改、付费请求或新阈值验收。
