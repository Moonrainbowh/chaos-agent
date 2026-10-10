# 第二次真实调查独立监督

结论：**PARTIAL_WITH_BLOCKERS**，候选 `463dc98099e3275a70daf896507b02d4ae387ae9`，owned case `v2-41808c269767`。用户批准的新12回合/40工具样例没有完成：实际 harness exit0、284.432秒，但 SYSTEM 为 **cancelled / unchanged / unknown**，原因是有界尝试期限取消。没有付费重跑、放宽质量门或覆盖 v1。

独立 reviewer 脚本 `investigation_v2_independent_review.py` 实际 exit0。读取本次自有 SQLite 使用 `mode=ro`，核对持久 usage/window/request 与收集值；原源码七文件 before/after/current hash 全相同。本次监督没有 Provider 请求。

## 实际成立的范围

一次显式 manual new_context 与换窗真实发生；换窗后执行了一次 notes_read_file 和一次 history_read_item。笔记读取的 **output.text** 精确等持久 note.content。历史读取 **output.text 经 json.loads** 精确等换窗前原始 message 对象；内嵌 read.output.text 精确等 current-contract.md 原始文件字节解码（包括 CRLF）。因此来源正文恢复和笔记恢复真实成立。作者 `offline-review-v2.json` 原来将这两项判为 FAIL，原因是离线 collector 字段/序列化比较，不能据此报告产品恢复失败；独立证据保留修正边界。

父预算12回合/27已执行工具；真实11个 HTTP wire 均 GLM5.3-flash/medium/max_completion_tokens4096。真实委派1次，子关系 parent_thread_id 精确等父 thread，lifecycle queued/running/failed 是实际事件。child 配置为 medium/may_write=false，但配置正确不等于真实 child 成功。

## 必须保留的阻塞

- 11个唯一 usage id 中10个 settled、1个 pending。已结算 input93040/output1752 精确等父预算；pending reserved **80703 token** 尚为未知负债，不能说全部请求 usage 完整，也不能计为零或猜最终费用。所有记录的 origin/owner 都是父 thread，没有重复相加。
- child 的直接 TaskRecord/TaskBudget API 投影为空，但 **raw SQLite 确有一条 context:child_budget**：id `15f7942624c5557692f7f12040ab2a19`。独立核对 owner_thread_id、parent_task_id、delegate_request_id、owner_instance_id 全等真实父任务、委派请求与运行实例；局部限额30000 token/4工具。worker 的 bindings 空数组来自当前投影收集，不能宣称生产缺少预算绑定。独立首次报告把投影空值当作没有绑定，已纠正并复验 exit0。直接 child task snapshot 未持久化属于该 child 路径设计/物证边界，不自行定性为产品故障。
- child 原始事件到首次 model_started 后即 `error/code=engine_limit/error_type=EngineLimitError`，没有 model_event/Provider usage。源码预算守门先于 HTTP；本次不是模型拒答或 paid child 请求失败。固定30k ceiling 与输入 prefix、4096输出和16000 safety 的本地预留关系待根因核算；没有通过提高限额来改本次结果。lifecycle 的0token/0tool不是子任务成功，正确绑定也不替代完整 child 成功验收。
- 没有最终来源调查交付；保存笔记仍有来源/测试行号+1偏移，不能把成功恢复这些字符串当作内容质量正确。当前仅证明指定小项目的手动换窗与原始证据恢复，不代表数小时长调查、自然窗口容量轮换、一般子任务或性能提升。
- parent-real.jsonl 混有6行非JSON的原生 subagent UI输出；reviewer 按JSON事件读取，同时明确记录这些行，未把无效行伪装为事件。过程 exit0不是SYSTEM completed。

整体 S16 仍不放行。真实子失败与 pending 负债的离线根因诊断可以继续；额外 Provider 尝试须遵循已有授权边界，不能通过修结果或继续同一次请求制造 PASS。
