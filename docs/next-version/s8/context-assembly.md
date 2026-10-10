# S8 显式上下文装配实施证据

本子任务仅修改主工作区，未改候选 worktree、提交、推送或启动实际 Host/Provider。S8 独立监督与完整套件由主 Agent 汇总，本文件不等同 S8 放行。

## 行为

- `ContextAssembly` 显式交付 builder、单个 BudgetedWindowClient、context actions 与 root-bound semantic snapshot；默认 semantic、boundary、persistent 与冻结 child 均使用这一结果。
- `engine_for` 按结构字段组装；main 初始与 profile 切换经 `map_builder` 保留能力，再添加 peer 展示。移除 production 对 builder/dispatcher 私有 `_inner` 链的猜测。
- `ContextScopedDispatcher` 在 ContextVar scope 中调用原 dispatcher 的公开 tools、action resolution 与 dispatch。原 RestrictedDispatcher、typed preflight、中央 policy 和 workspace 路由仍执行。Root 在原 policy 后取该 scope 服务；TaskScoped 构造 Root 同样看到该 scope。并发 child 或运行配置不会覆盖共享 Root 属性，异常/取消 finally 恢复 scope。
- 主 context actions 根据实际 thread 的 root 选择与该 turn 相同的 assembly；persistent remaining/history tools 使用活动 builder。Child assembly 固定授权 root，snapshot 访问错误 root 明确拒绝。
- 默认 summarizer 与主 Provider 使用同一个 guard；保留 S7 的持久实际 usage admission，未新增 summarizer/model 实例或 Agent loop。
- Child factory 在分配 Provider 前拒绝非 typed parent、没有具体 task_id 或非 typed authorization。Provider 切换和 child 的 partial close 沿用既有策略；同步 initial runtime 新增相同 retirement，任何 BaseException 都关闭已分配 client并保留原异常。

## 兼容边界

`build_context_runtime` 默认仍返回已有 builder，显式 `return_assembly=True` 供生产工厂读取公开装配结果；生产自定义 context factory 必须接受此选项并返回 ContextAssembly。`build_managed_context` 返回 assembly，其公开 build/compact forwarding 保留直接集成使用。`wire_managed_engine` 仅保留直接 assembly 兼容接线，拒绝私有 wrapper 搜索；生产不调用它。

旧构造/切换测试通过显式 snapshot capability 验证与 shared RepoIndex 的同一 immutable snapshot，不恢复已删除的 `_inner` 探针。Structured 请求完整传递测试的 fake 工厂同步返回 assembly。

## 已验证

- `context-assembly-final-313.log`：主源码、候选 locked CPython 3.13.2，37 项全部通过，12.618 秒。
- `context-assembly-final-311.log`：同范围 CPython 3.11.15，37 项全部通过，24.782 秒。
- 37 项覆盖新显式三策略、source/task/child snapshot 根分离、persistent 活动 builder 服务、真实 Root policy 路由、并发/cancel scope 清理，既有默认 summarizer 持久索引、persistent 两次 reset、生产 child 累计预算/实际 root、owner cleanup、构造/profile 切换、context factory 时序，以及 main/child/initial partial Error 和 CancelledError 关闭。

实现文件：`chaos_agent/context_assembly.py`、`application_context.py`、`context_runtime.py`、`managed_context.py`、`runtime_extensions.py`、`runtime_controls.py`、`runtime_provider_controls.py`、`runtime_dispatcher_factory.py`、`action_dispatcher.py`、`peer_context.py`。

测试文件：`tests/test_explicit_context_assembly.py`、`test_thread_intelligence_runtime.py`、`test_managed_context_runtime.py`、`test_persistent_runtime.py`、`test_agent_app_modes.py`、`test_agent_app_construction.py`、`test_runtime_partial_build_cleanup.py`。`test_child_execution_scope.py` 与 `test_context_runtime.py` 运行但未修改。
