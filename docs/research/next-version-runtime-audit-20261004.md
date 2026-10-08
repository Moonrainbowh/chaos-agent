# 下一版本 Runtime / 任务恢复审计

日期：2026-10-04。源码基线：`5793c72f4b0509d488fa9434e2f43e6ba838fbcd`，含当前工作区；未触碰既有 authentication 改动。本文是规划产物，没有修改产品实现，没有调用真实模型或读取/写入用户会话库。代码位置均相对仓库根。

## 判断

下一版不应另造 Agent loop、数据库、通用工作流引擎或“更强恢复框架”。已有 Core、Sessions、Workspace、Runtime 能力相当完整，真正的问题是这些能力没有在全部生产路径上保持同一份语义：工具名称合并后监督器仍识别旧名，旧工厂有父动作归属而新工厂没有，任务恢复检查与状态转换分开，子任务租约与实际执行预算分开。

最值得做的是把“一个任务如何开始、使用哪个工作区与授权、执行了什么、如何确定结束、如何恢复”收敛成一个所有入口都消费的应用服务。保留现有底层实现，减少入口与组合分叉；通过真实生产组合的离线故障注入验收，比增加新模式或角色更重要。

## 现有主路径与值得保留的能力

1. `IntegratedForegroundTaskController.start` 创建持久 TaskContract/TaskBudget 并绑定工作区（`chaos_agent/foreground_tasks.py:117-153`）。同一个 thread 的恢复会还原冻结 runtime，终态新目标走历史延续而不复活旧任务（`src/code_agent/interfaces/task_controller.py:194-228`）。这比只存聊天摘要可靠，应保留。
2. `AgentEngine` 经抽象 model/context/actions/sessions 协议执行回合；原始消息、事件与结构化 TaskState 分开（`src/code_agent/core/engine.py:26-47,86-128`）。不要以 LangGraph 或另一套循环重写现成内核。
3. TaskBudget 及软租约使用 Sessions 事务持久化，主任务恢复不简单重置预算。保留硬上限与实际消耗；软收敛应统一可信进展来源。
4. 工具实际动作有 `ActionExecutionContext`，可以表达 owner、origin、task、request、parent request（`src/code_agent/core/engine_actions.py:148-158`）。这应成为所有主/子/CLI/ACP 执行的共同契约。
5. Windows Runtime 先挂起创建进程再纳管，运行结束还需证明进程树终止和输出管道收尾（`src/code_agent/runtime/local.py:147-159,224-240`）。POSIX/Windows 平台适配与输出/取消边界是必要复杂度，不应为减行数删除。
6. Checkpoint 是跨 Sessions 与 Workspace 的协调，而不是伪称多文件原子事务；Rewind 先 quiesce、锁内重新读权威 intent、恢复 rollback checkpoint、使缓存/证据失效（`src/code_agent/checkpoints/_rewind_recovery.py:91-127`）。保留该边界。
7. Workspace 原子写、POST 身份证明、foreign 冲突时保留用户文件与 durable mutation journal 属于核心资产。不要把底层文件归属保护误当“过度设计”。

## 八个主要发现

### R1 / P0：未知工具结果只挡住第一次恢复

**已复现。** `ForegroundTaskController.events` 仅对 `PAUSED` / `INTERRUPTED` 检查 unresolved tool calls；命中后把任务改为 `WAITING_DECISION`。第二次 resume 不再检查 unresolved，直接转 `RUNNING` 并调用 runner（`src/code_agent/interfaces/task_controller.py:97-108`）。

纯临时 SQLite + 无模型 ProbeRunner 的结果：第一次状态 `waiting_decision`；第二次 `running`；runner 调用 1 次；同一个 `write_file` 的结果仍为 `unknown`。

这证明“未知结果仍在，恢复门已解除”，尚不证明真实外部副作用被重复执行。不能据此写成已经发生重复写入。

**应改设计：** 把待执行、未开始、已拒绝、完成、失败、未知结果区分为 durable action 状态。恢复资格应由该事实决定，不能由 TaskStatus 恰好处于哪一项决定。对可查询结果的动作先对账；对 read-only 调用可安全重新读；对不可判定外部动作提供明确的用户处置结果，而不是通用“再试一次”。当前 `_unresolved_tool_calls` 只通过 assistant/tool 消息配对推断 unknown，不能区别未执行、审批暂停与真实副作用不确定（`src/code_agent/sessions/_recovery.py:50-60`；`src/code_agent/core/engine_actions.py:40-45,72-77`）。

**验收：** 未解决 unknown 时，任意多次 resume 都零模型/工具执行；显式处置和对账形成可审查记录；未开始动作不被误报已发生未知副作用。

### R2 / P0：执行 owner 是审计记录，还不是可可靠取得的独占租约

**两个存储行为已复现。** 任务先 transition RUNNING，再单独 register execution（`src/code_agent/interfaces/task_controller.py:107-112`）。注册用 `ON CONFLICT ... DO UPDATE` 无条件覆盖旧 owner（`src/code_agent/sessions/_task_execution.py:26-34`）；stale reconciliation 对 task_executions 使用内连接（同文件 `:43-52`）。

探针表明：注册 owner-one 后 owner-two 可直接覆盖；构造“RUNNING 已提交、owner 尚未注册”的进程退出窗口，reconcile 返回未处理，任务仍 running。

当前 TUI 的前台槽与进程内锁有帮助，但不能替代数据库里的跨入口、跨进程取得执行权。此处还没有做两个真实产品进程竞争同任务的压力测试，不能把潜在竞态描述成已观测的双写。

**应改设计：** Sessions 用一个事务完成资格检查、持有者 CAS、RUNNING 迁移和 run identity 创建；退出持久化 outcome 并释放/结束 run；启动收集无 owner 的残留活动任务。仅需扩展现有 SQLite，不需要 daemon 或新队列系统。

**验收：** 两个入口同时 resume 同任务只能一个成功；状态提交各断点强杀后可恢复；仍存活的 owner 不能被另一个实例覆盖。

### R3 / P0：生产子 Agent 工厂丢失父执行上下文，显示出重复组合已发生行为漂移

`runtime_controls.compose_runtime_controls` 实际创建 `RuntimeDispatcherFactory` 并 attach 子运行时（`chaos_agent/runtime_controls.py:74-83`）。该 factory 的 `child_engine` 只接收 `agent`（`chaos_agent/runtime_dispatcher_factory.py:48-68`），`application_context.engine_for` 不传 `action_lineage`（`chaos_agent/application_context.py:303-318`）。

相比之下，另一套 `rewind_sessions.build_child_engine_factory` 接收 parent，并创建 `ActionLineage(owner_thread_id, task_id, request_id)`（`chaos_agent/rewind_sessions.py:163-195`）。现有 lineage 集成测试使用的是这个旧工厂/自定义工厂（`tests/test_rewind_lineage_integration.py:93-123`），不能自动覆盖新生产工厂。

**已用真实生产 factory、fake model/context 实例化验证：** 新工厂返回的 engine `_action_lineage is None`。同时 `EngineChildRunner.run` 只向 Engine.run 传 thread/cancellation、不传 TaskRecord（`chaos_agent/child_runner.py:75-85`）；`TaskScopedDispatcher` 在 authorization 为 None 时回到 source root（`chaos_agent/task_dispatcher.py:83-104`）。因此父任务在 managed worktree 中时，不能假定子任务工具天然留在父工作区，父 generation/owner 归属也不完整。

**边界：** 本轮没有让真实 child 写入 source/worktree；“工厂丢 lineage”是实证，“managed child 错写哪个具体文件”是调用路径推导。

**应改设计：** 所有 engine 构造用一份 Host factory；child execution 必须明确携带父 authorization、workspace、lineage、budget lease。删除旧构造路径前先迁移它独有的保障，不能机械选择“新文件”。

**验收：** 使用真实 application/runtime factory，在临时 source+managed worktree 跑 fake child write，校验只改授权根、owner/task/parent request 正确、父证据失效；ask/plan 父任务不能经委派得到写权限。

### R4 / P1：委派的预算与终态没有端到端兑现

子调用的 token/tool/time 租约在 `ChildRunRequest` 中存在，但 runner 工厂只收到 agent，执行时没有用 request 的三项预算限流（`chaos_agent/child_runner.py:68-85`）。实际 engine 上限来自 profile/mode（`chaos_agent/application_context.py:291-300`）；本轮 fake profile 的真实工厂实例输出 token limit 800000、tool limit 128，与一次请求的 1 token / 0 tool / 1 second 租约无联系。

`BudgetLedger` 是进程内状态，完成后才比较超额并按租约上限计费（`src/code_agent/orchestration/budget.py:55-63,98-118`）；exception/cancel 分支释放租约并按零计费（`src/code_agent/orchestration/supervisor.py:94-111`）。`SubagentRuntime` 为 parent_id 创建独立默认 ParentBudget（`chaos_agent/subagents.py:242-248`），每次 foreground events 收尾调用 release 后 pop supervisor（同文件 `:261-264`，`chaos_agent/foreground_tasks.py:264-273`）。这不是“同一持久任务的子费用始终占用父预算”的完整实现。

**终态已复现：** fake engine 只产出 CANCELLED 事件，生产 `EngineChildRunner` 返回 `completed` 和“无最终消息”的文本；其循环只处理消息/用量等事件，退出后固定构造 COMPLETED（`chaos_agent/child_runner.py:85-120`）。

**应改设计：** 先做好一个通用受界委派：父任务同一持久账本预留，child 的执行上限取租约与 profile 上限交集，取消/失败也结算已知费用，未知费用保留预留；以 terminal event/structured outcome 决定终态。目前不宜进一步增加 Oracle/Librarian 角色矩阵或后台调度。

### R5 / P1：合并 Tools 后，停止策略仍基于旧工具名和消息形状

`_observe_tool_only_convergence` 把模型原始 `turn.calls` 直接送 guard（`src/code_agent/core/_engine_convergence.py:37-46`）。但 `_PROGRESS_TOOLS` 只认 `write_file`、`replace_text`、`apply_workspace_edit_plan_v1`、`run_verification`、`new_context`（`src/code_agent/core/exploration_repeat.py:115-121`）；默认用户模型表面使用合并后的 `write` / `edit` / `execute`。

**纯函数已复现：** 连续五个无正文 `write` 调用使 guard 返回 `finalize`；同样五个 `write_file` 返回 None。`ExplorationRepeatObserver` 也只认旧 read/list/search 名称（同文件 `:11,133-135`），与工具外观重构不一致。

此外，tool-only guard 的设计本身承认“多个不同只读工具不是循环证明”，但五轮仍强制总结（同文件 `:30-38,90-108`）。把是否说了正文当进展，会给“多说一句继续调查”而非完成任务带来奖励。

**应改设计：** policy、TaskState、metrics、收敛与验证统一消费 Host 已解析的 canonical action + structured result；优先依据相同失败指纹、相同结果、代码/验证进展与真实预算决策。保留成本硬上限，去掉重叠的按消息形状硬停分支，先用离线场景比较效果。

### R6 / P1：验证系统很重，产品默认却关闭结构化验证

`structured_verification_enabled()` 默认 false（`chaos_agent/verification_mode.py:6-8`）；默认修改任务只要记录到 files_changed，就可 COMPLETED，stop_reason 为 `task completed without structured verification`（`src/code_agent/core/engine_completion.py:14-28`）。这是代码明确的选择，不是没有写验证模块，也不是模型测试命令完全不能运行。

现有 generation/subject/evidence/finalize 的不可自证完成设计值得保留；但不能将其默认能力写成“所有完成均有结构化验证”。模型自行执行 shell tests 与系统 verified evidence 仍是两层证据。

**应改设计：** 先决定一个简洁、可用的默认验证契约：展示“已改/已检查/未运行/受阻”，从已识别项目选择最少必要验证；保留只读和文档的低成本完成路径。不要只把 env flag 改为 1，也不要默认全量 tests/build。进阶语义图驱动的多阶段计划，须由实际质量/成本基线证明价值后再默认启用。

### R7 / P2：长期运行有历史窗口，但核心仍反复全量加载历史

每轮租约进展计算调用 load_messages 并反向找最近工具消息、数所有 user 消息（`src/code_agent/core/_engine_run.py:158-198`）；上下文构建又读取全部消息（同文件 `:222-228`）。`load_messages` 执行全量 SELECT/fetchall/decode（`src/code_agent/sessions/_thread_content.py:130-141`）。恢复清单再一次加载所有 message/event/checkpoint/note（`src/code_agent/sessions/_recovery.py:13-40`），`load_events` 无界 fetchall（`src/code_agent/sessions/_thread_content.py:229-240`）。

已有分页 API（同文件 `:143-180`），所以不需要另一套历史库。随着回合累计，该路径有重复扫描历史、累计工作近似二次增长的结构风险；本轮未跑超长会话性能压测，不给虚构延迟数字。

**应改设计：** 最近 action/result、user revision、unresolved action、message/event cursor 用事务维护的小投影；上下文消费当前窗口 + 按需历史；恢复摘要用聚合/分页查询。验收一百、一千、一万回合规模下恢复延迟、内存、每次新增回合的读量。

### R8 / P2：真正应删的是重复组合与已失效控制面，不是底层恢复保障

明确的候选：

- `chaos_agent/subagent_runner.py` 与 `chaos_agent/child_runner.py` 都定义 EngineChildRunner；全仓 Python 引用扫描中生产 import 指向后者，前者无引用。确认对外 API/打包约束后删除旧实现。
- 合并 `chaos_agent/application_context.py:280-318` 与 `chaos_agent/rewind_sessions.py:106-153` 两份 Engine 构造规则；当前 token budget 和 lineage 行为已不同。
- `SQLiteSessionRepository` 通过多 mixin 聚合不是必须推翻的架构。真正应明确的是“原子开始/完成/动作落地”的事务入口，而非将大量小文件重新拼成大文件。
- legacy low/medium/high/ultra mode 已与 topology/profile/effort 三轴并存，子角色又按模式路由（`chaos_agent/agent_modes.py:92-101`）；前端已隐藏 mode，但后端继续维持模式与独立 runtime 的双重心智模型。下一版可将四档收敛成兼容迁移入口；默认 UI 只保留模型、思考深度、是否允许委派。具体迁移须保留旧会话冻结语义。
- 独立父子关系、消息树分支、workspace lineage 各有不同含义，不应物理强行合表；产品层应减少把这些概念同时暴露给用户。

## 两个旧问题已修，不应再次作为缺口

根 `工作区的问题清单.md` 仍写“截至 2026-09-01 暂缓”，已与实现脱节：

1. **持久化文件身份已实现。** `chaos_agent/rewind_edit_batch.py:105-111,179-185` 先记录 POST 证明，`src/code_agent/sessions/_edit_batch_writes.py:90-107` 写入 device/inode；`src/code_agent/workspace/_batch_recovery.py:199-239` 要求 POST 身份匹配，否则 foreign。仍明确保留“写入完成至身份落库前崩溃，不能证明归属所以拒绝恢复”的保守窗口，这是限制，不是原先按同字节误归属的旧缺陷。
2. **取消时返回恢复结果的路径已实现。** `chaos_agent/rewind_edit_batch.py:126-138` 返回 recovered 结构结果；`src/code_agent/core/engine_actions.py:78-96` 先持久化真实结果再取消；`src/code_agent/verification/task_service.py:339-354` 识别 `workspace_may_have_changed`，`:194-195` 更新 generation。当前有对应取消和部分改动测试。本轮未复跑完整 Windows crash + foreign + verification 端到端矩阵，不扩大验收声明。

建议把旧问题清单更新为“已实现，附验证边界与链接”；本轮没有修改该文件。

## 建议迭代顺序与停止条件

| 阶段 | 目标 | 纳入 | 不纳入 | 交付/停止条件 |
|---|---|---|---|---|
| P0 恢复闭合 | 任意入口恢复不会绕过未决事实 | R1、R2；统一启动/恢复/结束事务 | 新守护进程、新工作流系统 | 多次恢复、双实例、强杀断点全部有确定 outcome |
| P0 生产组合一致 | child 继承真实父上下文 | R3；单一 engine factory；source/worktree 对照 | 新角色、增加并发 | 真实 factory 的 fake child 能证明正确 workspace/authorization/lineage |
| P1 可兑现委派 | 有界、取消真实、费用真实 | R4；同一持久预算、真实终态 | 多 Agent 自动组队 | 子请求上限真实执行，暂停/重启不送额外预算 |
| P1 减少错误硬停 | 工具表面变化不改变监督语义 | R5；canonical Action observation | 继续叠加文本启发式 | raw/merged/plugin 的等价动作产生等价状态；长只读任务不无故五轮停 |
| P1 默认可理解验证 | 用户知道实际检查到了什么 | R6；风险适配的最小验证与显式结果 | 无差别全量测试、复杂计划默认开启 | 常见 Python/Node/.NET 与文档任务覆盖；成本可解释 |
| P2 长任务成本与代码收敛 | 稳态开销受界、只维护一套构造 | R7、R8；分页投影、删除无用兼容路径 | 重写存储、OS Runtime、所有 checkpoint | 性能基线可重复，历史会话读取兼容，生产与测试走同一工厂 |

## 本轮验证证据与边界

现有测试：

```text
python -m unittest code_agent.interfaces.tests.test_task_controller code_agent.orchestration.tests.test_budget code_agent.orchestration.tests.test_supervisor tests.test_subagent_integration -v
Ran 30 tests in 2.599s — OK

python -m unittest code_agent.core.tests.test_engine_action_cancellation code_agent.core.tests.test_task_state -v
Ran 5 tests in 0.008s — OK
```

另外运行两个临时离线探针脚本，没有产品代码修改：

```json
{"case":"unknown-resume","first_status":"waiting_decision","second_status":"running","runner_calls":1,"unresolved":[{"tool_call_id":"unresolved-write","tool_name":"write_file","status":"unknown"}]}
{"case":"owner-overwrite","owner":{"instance_id":"owner-two","owner_pid":456,"owner_create_time":2.0}}
{"case":"running-without-owner","reconciled":false,"status":"running"}
{"case":"cancelled-child-event","result_status":"completed","summary":"Child run completed without a final advisory message."}
{"case":"production-child-factory","action_lineage":"None","engine_token_limit":800000,"engine_tool_limit":128}
{"case":"progress-tool-name","merged_result":"finalize","raw_result":null}
```

现有 35 项通过和这些新复现并不矛盾：原测试侧重单元与定制测试工厂，没有覆盖连续恢复、生产 child 工厂、合并工具后的监督等组合路径。本轮不宣称完整回归、真实模型、跨平台终端、长时间稳定性或真实双进程竞争已经验收。

历史恢复记忆只用于定位“已有能力与旧问题”，结论均重新核对当前源码。没有依据旧文档把已有功能列成新增需求。
