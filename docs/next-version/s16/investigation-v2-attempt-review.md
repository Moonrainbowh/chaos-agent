# S16 v2 真实尝试：取消、用量不完整、child局部预算拒绝

**验收不通过，不再发Provider、不修改产品预算。** v2 `next-version-s16-investigation-v2-41808c269767`按用户授权独立采用12回合/40工具；原f24c的8/24失败不变。外层exit0、284.432秒，SYSTEM为cancelled/unknown，原因是280秒开始的attempt deadline清理。父owner累计12模型回合/27工具，硬限12/40未放宽；生产standard lease工具阶段值30是自动值，harness没有注入。未出现最终完成结果。

## 实际多窗与恢复

一次new_context/request/window关联与reason=requested成立；初始窗0→持久窗1，原消息source digest从只读DB重算一致，同parent task/thread，累计预算不重置。新窗重复加载6契约并额外加载read/history，再读取来源；随后**实际notes_read_file与history_read_item都有完整工具结果**，note正文与旧write正文相同，history返回JSON与原source消息完全相同。这两项实际恢复在v2通过，与v1仅提出但未执行恢复的失败不同。

原始工具Message.content是包含output/is_error/metadata的ActionResult JSON；v2离线收集器必须展开output再比较，不能误判为缺少正文。v2所有七个源文件hash不变。没有测试执行或实现修改。

## 实际child与收集API限制

实际delegate_agent一次，custom sourceaudit queued→running→failed，mode=medium/may_write=false，活动约1秒；真实child relation.parent_thread_id匹配父。child没有TaskRecord是当前生产设计；授权由父冻结workspace/no-network/no-outside、不可变manifest may_writefalse/四只读工具、生产ChildRunner._parent_authorization剥离write/execute的链推导。未持久化直接child授权快照这一限制保留；不伪造child task。

worker-result的child.task/budget=None及bindings=[]不能等同实际无预算绑定：task None使worker跳过预算读取，list_checkpoints对普通历史checkpoint的展示过滤也遗漏内部预算checkpoint。只读原DB中实际`context:child_budget`完整存在：owner_thread_id=`2f37ef5359174a3da0f62215fbc0ee8d`、parent_task_id=`c3658e85238d4ef48325a5301c569c06`、delegate_request_id=`call_6a83440e0bf84c6491123d6c`、owner_instance_id=`10553bcfd915464c8427a13393f7a6b7`、max_total_tokens30,000/max_tool_calls4。binding id=`15f7942624c5557692f7f12040ab2a19`。不能据收集器空数组诊断产品未绑定。

child事件真实到context_built→model_started→error(code=engine_limit,error_type=EngineLimitError)，没有HTTP调用或usage记录。delegate返回advisory=true/status=failed/result failed/usage.complete=false/known_lower_bound=true，summary空/error=execution_error。不是已完成child，也没有真实子调查结果可审。

## 精确离线复现：30k无法容纳首请求

`diagnose_child_admission.py`在新自有clone `child-admission-offline-299ea618008a`创建原Host与公共task owner/session，调用实际SubagentRuntime→原EngineChildRunner→原生产ModelClient，HTTP send全部拒绝；没有FakeModel、没有改candidate/个人配置/原样例数据库。仅在本次诊断进程观察原BudgetedWindowClient.preflight返回与原Engine异常处理的传入exception，返回原结果不改逻辑。exit0/HTTP attempts0。

原生产prepare得到本地input estimate **13,311**、output reserve4,096、policy safety16,000；首累计reservation **33,407**，大于child局部30,000。该数值来自复现生产prepare结果，不是从真实context事件prompt_tokens13,074倒推；复现有独立thread/root，不能冒充原失败请求未留存的精确serialized estimate。异常堆栈明确：`client.py:77`转为`EngineLimitError("child task token budget has insufficient remaining capacity")`，由`_context_journal.py`按`spent+estimate > min(policy.task_tokens,binding.max_total_tokens)`拒绝。原真实事件时序与复现一致，child首Provider前正确拒绝，局部budget30k无法容纳这个场景。

因此停止“修产品预算以过样例”方向：这是fixture规划不足与现有安全预算的正确拒绝，不得降低safety/output、改token估计或扩局部30k来补PASS。必要预检应在**收费前**用真实Host/原client public prepare+全部source rules/schema计算child首完整reservation，并核局部剩余额度；仅catalog/model/readonly声明检查不够。后续若用户另外授权不同child预算的新样例，应另冻规格，不能覆盖本次。

当前公共错误投影把engine_limit折叠为execution_error（Core ResultCollector ERROR及child_result泛异常处理），故单读delegate结果无法判因；这可作为独立诊断质量改进候选，最小范围是Core result投影与Host child_result保留已有安全错误分类。它不是预算拒绝错误，不是本次通关必要修复，也不授权修产品或新的收费执行。

## 用量与最终prompt上限

11次HTTP安全元数据均GLM5.3-flash/medium/output4096；owner usage 11个唯一ID，其中10 settled、最后1 pending。settled实际input93,040/output1,752仅已知下界，总94,792；末pending reserved80,703保留未知负债，**不是实际消耗**，不能报告完整费用或把child缺记录当完整0结算。child首model turn虽未HTTP，已在共享owner计为1轮，因此owner12轮与11次HTTP不矛盾。

10个settled prepared input estimates24,273/30,786/45,192/26,922/30,991/31,909/35,472/36,490/54,889/58,942均在Host有效输入cap284,000内。末pending reservation80,703包含input/output/safety；不把它当Provider usage。window event979,904仍只表示1m工作窗减output/safety，最终BudgetedWindowClient保Host300k-safety16k=284k；复现原public prepare也读得host_prompt_tokens300,000/effective_input_cap284,000。没有临界容量/性能实测。

## 来源质量门与交付

没有稳定成功child，也没有SYSTEM completed父结果，五条最终path:line/行为/测试映射不记PASS。严格基准不变：代码names.py:2仅list(values)，保持顺序/重复且不改输入，但不trim/omit blank；current-contract.md:2-6为五条约束；test_trim:5/test_blank:6失败，test_duplicates:7还因trim失败，test_no_mutation:8-9应通过；顺序没有独立测试，只能指出隐含覆盖。legacy-notes:2-4是废弃排序/去重建议。模型文本不得人工补正或冒充测试实测。

纯离线收集器`review_real_investigation_v2.py`输出owned/offline-review-v2.json；只读SQLite mode=ro/query_only=ON，不调用Host/Provider、不修改状态。总体STRUCTURE_FAIL_REQUIRES_REVIEW。请独立审查核对真实终态、pending负债与复现因果；本轮不追加收费调用。
