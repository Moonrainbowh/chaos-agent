# S16 独立 persistent 手动换窗与只读 child：真实尝试未完成

结论：**验收不通过，不重跑、不扩阈值**。`f24c076ce446`真实单次尝试的外层exit0只表示harness正常收集结束；SYSTEM结果为execution_status=paused、verification_status=unknown、stop_reason="tool call budget exceeded"。没有最终来源调查回答、没有真实child，不能给S16整体或子任务项记PASS。

候选`463dc98099e3275a70daf896507b02d4ae387ae9`；实际入口公共Host TaskService；独有persistent策略与既有semantic样例分开。真实父model为glm-5.3-flash、medium、输出4096，6次HTTP元数据一致。六个owner usage ID唯一、全部settled；实际input51,707/output1,545，总53,252，无child重复相加。外层157.66秒，源目录七文件hash不变。审查器仅标准库读取，SQLite URI mode=ro/query_only=ON，未导入Host或Provider。

## 已证明与未证明

- 已证明一次真实`new_context`及持久request与window关联：window number1/reason=requested/source_start1/source_end16/start_sequence17，source_digest从同父原始消息重算一致。真实后续请求context事件window_number1；初始window0与后续window1组成两窗。
- 已证明同parent task/thread且预算累计不重置：六次构建的model_turns=1,2,3,4,5,6；工具累计0,6,10,12,18,22。最终model_turns6/tool_calls22，硬限8/24保持。
- 已保存虚拟调查note与真实历史ID；但**notes_read_file与history_read_item恢复仅存在模型提出的tool calls，没有对应工具结果**。不能把有效ID/旧source可查误作已恢复成功。
- custom声明式child配置预检同GLM/medium且may_write=false；真实`delegate_agent`调用0、children=[]、lifecycle=[]。真实child稳定终态、父归属、冻结授权和owner预算绑定均未验收，不能用预检配置替代运行事实。
- 自然容量耗尽、300k长文本性能与真实取消场景均未发生。本例手动切换部分物证不能证明这些未完成项。

## 工具预算失败的准确原因

|模型回合|持久assistant sequence|工具组|已持久工具结果|累计工具|
|---|---:|---|---:|---:|
|1|2|6×load_tool_contract|6|6|
|2|9|3×read + history_list_items|4|10|
|3|14|notes_write_file + new_context|2|12|
|4，新窗|17|6×load_tool_contract重复加载|6|18|
|5|24|3×read + history_list_items重复来源/索引|4|22|
|6|29|notes_read_file + 3×history_read_item|0|22|

第6轮模型输出已结算并持久assistant消息，然后Core `_reserve_tool_calls`按**整个4调用组**一次预留。22+4=26>24，返回HARD_EXHAUSTED并抛`EngineLimitError("tool call budget exceeded")`，发生在`_dispatch_tool_calls`之前，所以没有恢复工具实际执行，更没有到child。不是第25次调用已执行，也不是Provider报错；剩余2工具不能容纳4调用组。源码：`src/code_agent/core/_engine_turn.py:135`与`:340-358`。

重复操作是本次真实模型轨迹，不能删去或折算。PersistentContextBuilder明确新窗只携带最新user、引用，不自动注入旧对话/摘要/notes正文；生产GUIDANCE要求主动notes恢复。本样例prompt从头列流程且模型在新窗重复披露/源读取，说明这次流程执行没有在共享额度内完成。该物证不足以单独断言产品自动恢复缺陷，也不能按预计17工具代替实际22工具。

## 300k Host约束与window capacity

事件window_input_cap/prompt_budget_tokens=979,904来自model window1,000,000减输出4,096与安全16,000；它是builder的窗口容量展示，**不能宣称最终允许979k prompt**。冻结任务context_selection记录Host总prompt300,000；生产`RuntimeContextFactory._budgeted_client`将该Host上限写入RequestBudgetConstraints，`BudgetedWindowClient.preflight_request`在实际prepare/send前应用`min(API/work cap,Host300000-safety16000)=284000`。

六次settled usage.estimated_input为24,270、30,849、45,299、26,919、31,507、51,313，均≤284,000。这是prepared-json-v1本地估计，不是Provider actual input（合计51,707）；审计wire仅保model/effort/output安全元数据，未独立保存整个序列化请求body。因此已验证本次估计在Host上限内，但没有把窗口事件改名为prepared最终预算物证，也不声称测过临界284k边界。

## 复现与产物

`review_real_investigation.py`读取该owned的worker-result、attempt-result、事件观察与只读DB，不恢复任务、不修改会话、不发Provider；输出`offline-review.json`。总结构判断STRUCTURE_FAIL_REQUIRES_REVIEW，明确SYSTEM未完成、notes/history未恢复、child不存在。完整源回答质量须有真实最终回答才能审查；本次缺失。

旧`b5c33bd81b35`启动因objective长度被拒HTTP0，`d5eb1e1852f7`离线发现workflow title长度512限制HTTP0，两项原失败均保留；f24c是新的owned单次真实样例。当前停止在原失败，不扩大工具预算、不通过恢复或新请求补PASS。
