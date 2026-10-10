# S8 生产入口只读审计

2026-10-04；以当前 S7 放行后的工作区为准。只检查代码/契约/引用，未启动 Host、Provider、Shell 或任务；未修改产品代码。本审计是需求证据，不是 S8 验收通过。

## 结论

默认公开装配已经是 `chaos_agent.app.create_application`，TUI、CLI ask/JSON、SSH 手机与 PWA 执行共用 IntegratedForegroundTaskController。主要缺口是 ACP 直接调用低层 AgentController；CLI 本地任务读取又被完整装配前置的 Provider 配置与 Shell 探测阻塞。现有旧装配链仅剩测试依赖，可以先迁移保障测试，再删除孤立装配；Rewind 写侧仍是生产必需。

## 公开入口到实际执行

|入口|实际链与证据|当前边界|
|---|---|---|
|TUI|`cli.py:165` → `app.py:53` → `runtime_controls.py:74/139` → AgentController/AgentEngine；`ui_runtime_composition.py:125` 注入 IntegratedForegroundTaskController，`:159` 注入 TUI；`interfaces/tui_run.py:159/161` start，`:208` `_consume_task`|生产注入 tasks；无 tasks 的 UI 低层兼容分支仍存在，不能用它证明生产保障。|
|CLI ask/run JSON/task resume|`cli.py:201` → `interfaces/commands.py:138–160` start/events/resume →同 foreground/controller/Core/Sessions|冻结授权、恢复门、owner、结果读取已有共用路径。|
|CLI resume thread|`commands.py:163` → `task_controller.py:247` resume_thread|有 task 的线程走 events；无 task 的旧线程在 `:263` 直接 controller.resume；终态线程拒绝带 prompt 恢复。需共享任务入口明确映射旧线程的新任务，避免 taskless Provider 请求。|
|ACP stdio|`acp_cli.py:12` → cli acp → `acp_adapter.py:17` 取 application.controller → `acp/adapter.py:189` controller.ask(text, thread_id, cancellation)|**实际绕过 foreground**：未创建/恢复持久 Task，未取得 task execution owner；Core 无 task，冻结授权、持久 parent budget、恢复核对和 TaskResult 不能等同其他入口。ACP permission scope 不替代这些事实。|
|手机 SSH|`mobile_cli.py:18/34` →相同 app → `:36` compact TUI →同 foreground|按显式 root 重建；项目目录可在 `--check` 无模型读取，但打开项目/历史仍先完整 app。|
|手机 PWA|`remote/server.py` HTTP start/stop → `remote/task_controller.py:87` `_prepare` start/恢复/继承 →`:196` foreground.events →同 Core/Sessions；`:226`后读取 foreground.result|Host 单活动槽/事件游标是展示状态；生命周期仍持久 task。终态续聊新 task+新 thread，非终态复用原 task。|

默认装配 `app.py:197` 构造 RuntimeContextFactory，`:223` compose_host，runtime_controls 构造 runtime dispatcher/main engine；S7 子任务由 `runtime_dispatcher_factory.py:49` 从具体 parent execution/context/auth 组装，使用 owner-scoped Sessions、授权根、收窄 dispatcher 与实际 child binding。S8 必须沿用它，不能新增 loop 或用 ACP 私有 adapter 状态替代它。

## Session / Task / Attempt 与连续对话

- Session 对应持久 `threads` 与消息/事件/树/checkpoint；`sessions/models.py:69` ThreadSummary 为展示摘要。它承载对话，不负责执行授权。
- Task 对应 `core/task.py` TaskRecord/TaskContract、`sessions/_task_records.py:25` 创建；数据库 `sessions/_database.py:55` 对 thread_id 有 UNIQUE，现有模型每 thread 至多一个 Task。冻结 root/权限/profile/mode/budget、恢复与终态属于 Task。
- Attempt 目前没有独立持久 Attempt 表/类；`task_executions` 在 `_database.py:69` 保存当前 instance/PID/create_time，foreground events 生成 run_instance_id 并发事件，释放时删除 owner 行。应把 Attempt 定义为同一 Task 的一次 owner 执行窗口，不新建第二套状态机；需要保留历史可用已有事件/结果。恢复不重置 Task 累计预算或未知 usage 责任。
- `foreground_tasks.py:73/122` 支持空 thread 新建、空会话绑定、source_thread 继承消息另建线程；`:137` create_thread_from_history。不复制执行状态/预算/授权。PWA 已采用该映射；TUI同样在终态后继承消息新 Task。ACP 必须保持客户端 session ID 语义：可维护 session→当前 task/thread 的显式薄映射或明确返回新 session，但不能把客户端 ID 随意当成新任务 thread，也不能复活旧终态 Task。旧非任务历史遇新 prompt 应创建新 Task。
- WorkflowService 是 foreground 的投影与通知，不能成为跨入口第二个生命周期权威。

## 上下文显式装配缺口

`managed_context.py:59` wire_managed_engine 沿 context `_inner` 循环寻找 managed_client，并沿 dispatcher `_inner` 下钻设置 context_actions；`application_context.py:246` _ThreadRootContextBuilder 暴露默认 builder 私有结构，`engine_for:343` 调用猜结构接线。风险不是当前所有路径必然失败，而是包装顺序决定能否找到 budget client/actions，难以契约测试。建议上下文工厂显式返回窄结构（builder、model_client、context_actions）；Root dispatcher/actions 的绑定由创建它的装配处完成，Restricted/Plugin 包装不靠解包猜测。default/summary/boundary/persistent、source/task/child 与 runtime 切换都要走同一结果结构。不要删除 S7 默认 BudgetedWindowClient。

## 本地历史与诊断依赖

`cli.py:165` 对 task list/result/recovery 与 resume(无 prompt) 一律先 create_application/startup。`app.py:98` load_runtime_config、`:100` Windows Shell 探测、compose_host 插件/MCP、runtime_controls Provider 客户端和 UI 组装都发生在只读命令前。故 execute_command 本身是纯读取并不意味着公开 CLI 可离线读取。

`remote/catalog.py:64/221` 的 snapshot/message_page、MobileCatalog 和 `mobile_cli.py:62–78` --check 可以只用 Sessions/ProjectStore；但 remote server 的初次启动仍来自完整 Application。这些现有只读 repository/catalog 能复用，不应为历史另建模型/Shell。应在 CLI 解析后、完整 app 前选择窄本地读取依赖，覆盖 task list/result/recovery 与明确 history 读取命令；操作者 resolve 是写决定，应保持独立授权及本地 mutation gate，不能混入纯读取路径。

## 旧链引用清单与迁移条件

本次 `rg` 全仓 Python 引用核查（排除 docs/venv）得到：

- `app_factory.py` 只被 `tests/test_rewind_lineage_integration.py:42/300` 导入/patch `_build_execution`。`factory_host.py`、`factory_context.py`、`app_presentation.py`、`app_models.py` 的引用均来自这条孤立链，没有公开 CLI/mobile/ACP production 引用。迁移 `test_child_engine_and_compactor_use_root_scoped_repository`（`:260`）到真实 `app.create_application`/生产子任务入口，断言 owner/root/scoped context/verification/lineage 后可移除该链。
- `rewind_sessions.py:107` build_engine、`:161` build_child_engine_factory 只供旧 app_factory 与 `test_retained_child_factory.py:17`；`legacy_execution_budget.py` 只供该旧 build_engine。删除前把旧 factory 独有保障（typed auth+具体 parent、错误根分配 Provider 前拒绝、scoped Sessions、实际 usage admission、partial provider close）迁移生产入口测试。
- `subagent_runner.py` 的 retained runner 仅测试 `test_retained_child_factory.py:18`、`test_child_result_contract.py:7` 引用；生产为 `child_runner.py`。可以在生产 runner 对应测试全部覆盖后删除或仅保薄别名；不得保第二份执行实现。
- **保留** `rewind_sessions.py:216` build_rewind_write_side 与 RewindWriteSide/CoordinatedSessionRepository：`app.py:162` 实际生产使用其 write-side，删除整个文件会破坏写前/写后 journal、capture、协调 checkpoint 与 owner routing。
- 现有 `test_child_execution_scope.py`、`test_child_process_cleanup.py` 已从真实 app/生产 runner 使用 SQLite 与 root routing（进程用真实 Windows Job）；可扩展入口覆盖。ACP tests FakeController 与旧 `_build_execution` 单测不能作为“公开入口经过 S5–S7”的独立物证。

## 最小实施边界与验收

1. Interfaces 窄任务入口复用现有 start/events/resume/pause/interrupt/accept_partial/recovery/result，不复制调度；明确 session 连续映射，ACP 适配取消与 stopReason、permission scope、持久 TaskResult。
2. Host 显式上下文装配结果，覆盖默认与 opt-in、主/子/切换及失败关闭。完成 Feature 验证后回 Host 集成。
3. Host CLI 独立本地读取装配，Provider 未配置、Shell resolve 强制失败、插件加载强制失败仍可读取真实 SQLite 历史/task/result；执行入口仍要求完整依赖。
4. 先迁移生产入口测试再删旧链，最终 rg 无活引用。跨 TUI/CLI/ACP/PWA 相同读任务及实际写任务验证 root/权限、owner、S5未知动作恢复门、S7父子预算/未知usage、S6结果一致；验证取消等待真实 process/closer 后才释放 owner，以及终态续聊不复活 Task、恢复不重置预算。

不涉及 S9 收敛策略、全仓搬迁、提交/推送或 live Host 升级。

## ACP 稳定 Session ID 的最小映射建议

复用现有 checkpoint，不新增执行表：ACP 的客户端 sessionId 固定为首次新建的空 root thread；共享 `prompt_session(session_id, text, cancellation)` 解析其 Host-owned continuation checkpoint，获得当前实际 thread/task。新空 thread 首次 prompt 用 start(thread_id=session_id)；非任务旧历史用 start(source_thread_id=selected_thread)；可恢复 Task 用 events；终态用 start(source_thread_id=selected_thread)，写 checkpoint 标明 ACP session root/current thread/task，再 events。ACP update 始终使用客户端原 sessionId，工具事件里的 task_id 仍是实际 Task ID，二者不可混同。

当前 `create_thread_from_history`（`_thread_content.py:65–110`）把所有后续线程挂在两级 root 下，conversation_heads 保留消息树。这足以验证成员关系，却**不唯一标识 ACP 当前分支**：同 root 可能有人工消息分支或子 Agent。故不能简单选“全库最近 task”或“root 下最新 thread”。使用专门 checkpoint projection 明确选中的 continuation，重启/load_session 按该投影恢复；每次使用验证 current thread 的持久 root relation、Task 对应 thread、冻结/源 workspace 与当前应用同项目，不允许 child-budget-bound 的线程充当 ACP 主任务。投影坏/缺失且存在多个候选时明确拒绝/请求选择；缺失但原 session 只有自己一个合法 task 时可直接恢复原 task。

continuation Task 创建到 projection 保存的窗口应做失败补偿：未开始执行的新 CREATED Task 可转 INTERRUPTED，并把准确 task/thread 标识写入恢复提示；不能悄悄丢失而在下一 prompt 重建第二个 task。投影只负责选择，与生命周期/预算无关，cancel/close_session 对选中的实际任务调用 foreground interrupt，并等待同一 events stream 清理。`list_sessions` 返回 ACP root session（当前任务标题/更新时间可投影）；不要把主 continuation 和 child thread 都伪报为独立 editor Session。load_session 回放已选实际 thread 的消息，避免新 prompt 后旧 root 历史缺少续聊。

如果更小实现选择复用现有 conversation_heads 的唯一游标，需要先证明所有现有分支操作都更新同一个“所选主线程”事实；当前代码没有这样的唯一事实，单靠 parent_thread_id 不能完成此证明。专门有界 checkpoint 是较直接的薄适配，仍无第二个 Agent loop/调度器/Task 状态机。
