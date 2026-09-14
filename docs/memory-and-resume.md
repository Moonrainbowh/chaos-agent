# Resume 与记忆维护说明

## Resume 边界

`Task` 持有目标、权限、冻结运行时和累计预算；`Thread/Session` 保存消息与事件；一次执行是一个 `Run`，上下文切片是一个 `Window`。`ForegroundTaskController.resume_thread` 对任务线程走任务恢复路径，先恢复冻结运行时，再恢复同一 task/thread；终态任务只能查看历史，不能改回运行中。

执行 owner、任务状态、消息/事件游标和 checkpoint 由 SQLite 持久化。工具结果已经写入日志但 checkpoint 尚未推进时，恢复应复用已有结果；已开始但结果未知的副作用必须进入核对/等待决定，不得盲目重放。checkpoint 不会回滚工作区，工作区有未提交修改时保留并要求重新验证。

Task Notes 是派生的交接信息，不是权威状态。Notes 可以落后于原始日志，恢复时以消息和事件为准；笔记工具使用 `notes_*`，不会自动把全文注入上下文。

宿主可调用 `recovery_checklist(task_id)` 获取有界恢复事实：任务状态/原因、目标、消息与事件数量、最新 checkpoint 游标、Notes revision、排队 follow-up、执行 owner、未配对工具调用和验证证据数量。没有持久化 tool result 的 assistant 调用会标为 `unknown`，恢复不得直接重放；该读取不调用模型或工具，工作区是否漂移仍必须由 workspace/verification 服务核对。

## 记忆条目

SQLite `memories` 表以 `(scope_type, scope_id, memory_id, revision)` 标识条目。`scope_type` 为 `task`、`project` 或 `user`；默认只返回 `active` 条目并严格按作用域过滤。新增可用 `create_memory`，修订使用 `expected_revision`，冲突时调用方应重新读取；`set_memory_lifecycle` 用于 `active`、`withdrawn`、`archived` 等状态，`delete_memory` 才是物理删除。每次修订保留旧 revision，并记录 `supersedes`/`derived_from` 关系字段。

只有明确适用范围和来源的项目决策才应写入 `project`；当前任务待办、临时环境状态和未验证猜测留在 task Notes。`user` 作用域预留给明确允许跨项目复用的偏好，不得把项目事实写入其中。记忆内容始终是资料，不是系统指令，不能扩大权限或直接触发工具。

跨作用域检索使用 `search_memories(project_id, query, user_scope_id=..., allow_user_scope=True)`；实现先限定当前项目和显式授权的 user scope，再做字面匹配，只返回 active 条目。未传 user scope 时不会读取其他项目，也不会将历史、撤回或删除内容注入结果。

需要解释检索时使用 `search_memory_diagnostics(...)`，它只返回允许作用域、候选数量、选中 revision ID、排除计数和查询词元估计，不返回未选中条目正文或受限来源内容。

`assess_memory_applicability(record, context)` 将条目 lifecycle 与当前任务适用性分开：条件全部匹配为 `applicable`，已知条件冲突为 `conflict`，条件缺失为 `needs_check`，撤回/替代/归档条目为 `not_applicable`。上下文构建器默认以 `needs_check` 标注没有宿主事实核对的记忆。

项目经验提升到用户级必须调用 `promote_memory` 并显式授权；调用方需要提供已经去项目化的新正文，系统只保存 `derived_from` 关系，不自动复制项目历史或开放原始来源。
