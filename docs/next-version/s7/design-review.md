# S7 独立设计复核（只读，非放行）

范围：当前父子装配、权限、预算和执行 owner。没有修改产品、运行产品验收或审查 S8。

## 当前证据

- `chaos_agent/runtime_dispatcher_factory.py::child_engine(agent)` 不接收父 ActionExecutionContext；旧 `rewind_sessions.py::build_child_engine_factory` 接收它并设置 `ActionLineage`、`sessions.for_owner`。应修生产装配，保留旧工厂兼容。
- `chaos_agent/child_runner.py` 使用全局 current-thread 回调创建子线程；Core child 运行 task=None，`core/engine_actions.py::_invoke_action` 此时未传 TaskAuthorization。工具名称限制不能代替父权限。
- `SubagentRuntime.release` 删除 supervisor，下一次构造新的内存 BudgetLedger；supervisor 取消/异常 release 按零费用结算。已发生费用和未知请求因而可能被赠回。
- `context_windows/client.py` 已逐 Provider 请求预留完整输入、最大输出和 safety；`sessions/_context_journal.py` 在短事务内 reserve/settle，未知保留 reserved，settle 相同结果幂等。当前 owner 是单 thread，未覆盖父子共享和默认 semantic 主路径。
- `_task_budget.consume_usage` 在构造 TaskBudget 时拒绝超 hard cap；如果 Provider 实际超过预留，调用可能回滚。不能因此丢掉实际 Provider usage，也不能使用 min 截断真实消耗。
- `_task_execution.register` upsert 无条件覆盖现存 owner。

## 建议的最小接线

### 权限与工作区

生产工厂接受可选 parent context，使用其中的 task_id、owner_thread_id、origin_thread_id 从持久数据读取父契约。不可用当前 UI/root 选择替代父任务冻结的工作区。子线程属于父 context 的 origin thread；预算/写入归属指向 root owner thread。线程树超过既有两层限制时明确拒绝或使用现有 root 下兄弟线程，不静默换父归属。

冻结子执行绑定：父任务、owner/origin、parent request、child run ID、冻结工作区、父授权与 role/mode 限制的交集。绑定由 Host 产生，模型不能指定 owner。只读父任务下的 writable child 不获得写入/执行权；restricted dispatcher 与中央策略双层保留。

不要把父 TaskRecord 原样作为 child 的 Core task：thread mismatch，且 child completion/verification 会误推进父任务。可在 AgentEngine 增加可选 inherited authorization，供 task=None 的 child dispatch 使用；ActionLineage 和预算归属另传。子完成保持 advisory，不 finalize 父任务。

### 单一持久费用账

扩展已有 `context:usage` 请求记录，含 budget owner thread、origin thread、child run ID、purpose 和不可复用 request ID。主、子、semantic summary、managed handoff、persistent-window 请求都在 Provider 调用前使用同一 reserve。不能把仅 managed 配置测通称为默认 semantic 已受保护。

推荐 child.token_budget 为持久局部 ceiling；父 hard cap 按每次 Provider 请求原子预留。不要父预留整份 child quota，又将 child 的每请求预留计入一次。若选择整份 allocation，需要明确子调用从 allocation 内扣除，复杂度更大且不是最小方案。

共享准入公式：已知 actual + 未知 reserved liability + 所有进行中请求 reservation + 新请求 reservation <= 父冻结 hard cap；另检查 child 已知/未决累计不超局部 ceiling。失败、取消和重启不释放未知 liability。只有 Provider 尚未 dispatch 的 queued 请求才能安全释放。

settle 用 request ID 幂等记录 actual，去除该请求 reservation；实际超过预留仍完整保存 actual 并阻断后续请求。零/缺失 usage 保持 unknown，不写 actual=0。actual 与 reserved liability 分开展示，不能把 conservative liability 报成 Provider token。

settle 必须成为 managed 请求的唯一实际费用写入点，同时更新既有 task_budgets 的投影。Core `_record_model_usage`、semantic summarizer 与 handoff 的额外 consume 路径须显式识别 managed accounting 并避免再扣一次。可用少量可选 managed-accounting 参数，不根据数值相等去重。未接入守卫的旧调用保持旧接口，不静默禁用其计费。

### 子调度与工具预算

child run 的身份、计数、局部 cap、queued/running/settled 状态存现有 checkpoints；它们是请求/调度事实，不再建立另一套累计 token 总账。SubagentRuntime supervisor 可作为内存缓存，release/reset 不删除持久用量和 children-started 事实。重新开启相同 child run/action ID 返回已保存结果或阻断未决，不能获得新 quota。

工具调用使用现有 reserve_task_budget 原子归 root owner，同时检查 child ceiling；主 delegation 调用与子内部工具是不同真实调用，可以各计一次，须文档说明。unknown action 已预留次数不返还。active_seconds 先定义父墙钟或所有 agent 活跃秒，不能用并发子线程的不同累计值覆盖 parent 已存值。

### owner 与取消

register 事务中检查现有 instance；相同 instance 幂等，其他活 owner 拒绝；确认死 owner 后 CAS 接管。不用 upsert 直接抹去运行事实。缺 owner 的 active 旧记录明确核对/中断，不能直接假定没有运行。

沿用 S5 instance CAS release 与 S6 run-result，父取消等待子退出、费用结算和 workspace gate 释放；未知费用仍保持预留。不能用 supervisor 异常路径 release(0) 表示未消费。预算耗尽先禁止新调用，再取消并等待已有请求，不能认为取消撤销已发生副作用。

## 必要反例

1. 默认生产工厂与旧工厂：父 ask/plan 禁写禁执行、不同项目、managed worktree、compact/plugin 操作均不能越权。
2. 两个不同父任务同时创建子线程，UI current-thread 改变不串 owner/工作区；child 不推进父完成。
3. 父实际费用与两个并发 child 预留竞争同一剩余额度，至少一项原子拒绝；无法各获得完整独立额度。
4. summary/handoff/主模型/child 每项单独计入且只计一次；重复 usage settlement 幂等、冲突 settlement 拒绝。
5. 中途有部分 usage 后失败、usage 缺失、进程重启、runtime release 再 activate、重复 run ID，不赠 quota。
6. Provider 实际超预留时保留真实 actual、下一请求拒绝；报告区分 actual/unknown liability。
7. 父取消时子仍占用 workspace gate/Provider 请求：等待退出后发布边界；不把取消报成功，不丢费用。
8. 第二个 Host 登记 alive owner、旧 instance finally release、新 owner CAS 接管、active 无 owner 旧记录；不得覆盖或删除其他执行。

这是可实施设计建议，不替代阶段 diff、自测与独立监督。
