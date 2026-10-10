# 本次真实验收独审：INFRASTRUCTURE_BLOCKED / NOT_EVALUATED

审查对象为本目录单次执行。独审只读核对冻结文件、实际 wire、worker-result、supervisor、stderr、事件和父 durable；未启动模型、运行测试或修改生产代码。

实际 Host TaskService 已启动父线程 `ca25529188cb435996fac7bfb86b3574`，首个模型轮次在返回模型响应前遇到本地地址 `127.0.0.1` 的 `ConnectError: All connection attempts failed`，随后抛出 `ModelStreamError`；supervisor 记录退出码 1。此证据证明本次传输阻断，不能单凭它认定具体哪个服务或配置是根因。

三份 wire 是同一初始请求的首次传输及原有两次重试。三者实际 SHA256 均为 `6d3cb6113c4903d11352262682fe06096fe7678364af3860c36c41623576c291`，与 worker 记录匹配；均为 `glm-5.3-flash / medium / 4096`，消息角色为 system/user。它们不能计为三次完整模型验收或三轮模型响应。

父 durable 与实时事件均没有模型回复、工具调用或最终 assistant 报告；messages 只有原用户任务，`parent_review` 与 `parent_review_attempts` 都为空，`child_entry_observations=[]`。因此子委派、四来源实际完整读取、独立阶段/比较阶段隔离、精确引用、五行为判断、测试能力见证、current/legacy 区分、子报告纠错与中文交付均为 **NOT_EVALUATED**。不能以没有发生违反行为推导这些质量门已通过；没有有效终态报告，也不能宣称生产终态已验证为 unverified。审查自身对语义结论保持 unverified。

用量只有一个唯一记录 `3f55230f82d154a39e2481c77f3eb6dd`，状态为 **pending**，reserved 为 **40615**，父 owner 与 origin 都是上述父线程；无 settled usage 或 charged 值。记录中的 `projected_tokens=0` 和初始 budget 中 input/output 为 0 不能证明实际消费或结算为 0。本次结算 token 总数为 **unknown**，预留不能当已结算消费，也不能把三份重试 wire 或 durable 事件重放重复相加。freeze.origin 内历史用量不属于本次结算。

五个工作区文件（包含四来源）当前物理 SHA256 均匹配 freeze.workspace_hashes，且匹配 supervisor.after_hashes；supervisor 另报告 `candidate_unchanged=true`。已确认 fixture 完整保留，候选不变为 supervisor 记录，本独审未另运行候选检查程序。原预算、模型和重试配置没有从本次记录观察到提升；未发生额外模型重跑。

结论为 **INFRASTRUCTURE_BLOCKED / semantic_acceptance=NOT_EVALUATED**。本次不能用于证明修复后语义成功，也不能作为模型语义失败样本。失败记录应原样保留；若后续在用户授权范围内恢复传输并重新运行，应使用新的运行目录，不重写本次事实。
