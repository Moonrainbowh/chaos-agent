# S4：工具成本观测

## 目标

为每个任务建立轻量的 typed action 成本基线，先获得真实的任务级数据，再决定是否需要批量读取、减少重复验证或调整调度策略。本阶段不改变 action 的执行顺序、权限判断、取消语义或预算规则。

## 已观测字段

- action 总数、错误数和累计耗时；
- action 耗时的 p50/p95；
- 按 action 名称的调用计数；
- `git`、`workspace_read`、`workspace_edit`、`verification_or_process`、`other` 分类耗时；
- 同一任务中重复读取的次数；
- 失败 action 后再次尝试同一 bounded signature 的 retry 次数。

`ActionMetricsCollector` 只保留进程内聚合结果，不保存参数、输出或路径正文。路径只参与 SHA-256 指纹计算；没有 `ActionExecutionContext.task_id` 的 action 归入 `unscoped`。

## 接入边界

`RootActionDispatcher` 在 action 执行完成并附加权限 metadata 后记录指标，因此拒绝、取消和未开始的 action 不会被伪装成已执行 action。观测失败不应成为业务成功条件；当前 collector 对输入类型保持严格校验，dispatcher 的既有异常转换仍保留。

## 当前结论与后续门槛

本阶段只完成 instrumentation，尚无足够真实任务 trace 证明重复读取或验证成本应当被优化。后续只有在真实任务的 p50/p95 和重复率显示稳定热点后，才考虑 batch/fast path；CAS/Git 的取舍继续以 S3 benchmark 和实际任务数据为依据，不因主观感受删除 CAS。
