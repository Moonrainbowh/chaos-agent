# Agent Orchestration
以冻结的任务模式和可审计的父子关系组合多个独立 Agent 运行，在不复制单 Agent 内核的前提下提供有界委派。

## 边界
- 负责：定义并解析 `low`、`medium`、`high`、`ultra` 任务模式，将模式映射为已批准的主 Agent provider profile、提示策略和推理参数。
- 负责：严格区分 Agent 模式与权限模式；四档模式共享同一完整工具集合和 Ultra 级硬预算，模式只改变主模型、推理强度和编排策略，不改变 `ApprovalMode` 或 `ActionPolicy`。
- 负责：在新任务或恢复边界冻结模式、角色和资源快照；活动任务不得隐式换模型、提高预算或静默降级。
- 负责：声明主 Agent、通用 Subagent、Oracle、Review、Search、Librarian 和已验证自定义 Agent 的角色约束；子 Agent profile 按角色稳定路由，不从父模式降级推导。
- 负责：创建隔离的子 thread/task，并限制递归深度、并发数、子任务数、运行时间、模型、token 和工具预算。
- 负责：从父任务预算中租借并累计子 Agent 消耗；委派不能产生额外的无限预算。
- 负责：传播暂停、取消和失败；父任务终止后不得遗留继续执行的孤立子 Agent。
- 负责：以结构化结果、来源引用和父子事件向父 Agent 返回结果；Oracle、Review 和 Subagent 文本始终是建议，不是 verification evidence。
- 负责：同一工作区默认单写者；并行只读允许，写入型子 Agent 必须取得独占工作区租约或使用独立工作区。
- 负责：向界面提供只读 Agent 树、角色、状态、耗时、用量和来源投影，不负责具体渲染。
- 负责：对子 Agent 注册、开始和终止状态提供可订阅的有界生命周期快照；状态在执行开始前写为 queued、取得运行配额后写为 running，并在任何退出路径稳定收敛到 completed、failed 或 cancelled。
- 不负责：实现模型协议、创建 provider client、读取密钥、组装最终提示词、执行工具、保存 SQLite 或渲染界面。
- 不负责：复制 `AgentEngine` 回合状态机；每个父子运行仍复用 Core 的单 Agent 引擎和完成协议。
- 所有子 Agent 外部动作必须转换为 typed `ActionRequest` 并经过同一个 `ActionPolicy`；有效能力是父授权、任务授权、角色、模式和插件约束的交集。
- 子 Agent 成功状态或父 Agent 汇总不得直接产生 `COMPLETED`；任何写入仍须使所属 subject generation 的旧 evidence 失效。

## Units
- `ModeDefinition`、`ModeSnapshot`、`AgentDefinition`: 冻结任务模式、实际模型、角色、工具子集与提示策略 | 无副作用 | 模式不承载权限，快照可审计且活动任务不可变
- `ModeRegistry.freeze(...)`、`standard_mode_definitions(...)`: 解析四档模式并绑定已配置 profile、Oracle 和资源限制 | 无副作用 | 缺失 profile 失败闭合，digest 不包含密钥
- `snapshot_payload(...)`、`snapshot_from_payload(...)`: 在稳定 JSON 结构与模式快照间转换 | 无副作用 | 恢复后继续执行构造校验
- `BudgetLedger.reserve(...)`、`settle(...)`: 为并行子运行预留并累计父任务 token、工具、时间和 child 配额 | 维护进程内租约 | 预留防止并发超借，超额结果按租约上限保守计费并拒绝
- `ChildRunSupervisor`: 在并发上限内运行注入的单 Agent runner、传播取消并投影父子状态 | 创建 asyncio task | 只读可并行，写入使用单写者锁，异常文本不透传
- `ChildRunSupervisor.subscribe(listener)`: 订阅 queued、running 和稳定终态的 `RunView` 快照 | 进程内回调 | 观察者异常不得改变子运行结果
