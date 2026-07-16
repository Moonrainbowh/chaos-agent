# Sessions
把聊天、目标、动作、用量和 checkpoint 保存为可恢复、可迁移的结构化状态。

## 边界
- 负责：线程生命周期、消息与事件存储、持久目标、checkpoint 元数据、恢复和 schema migration。
- 负责：checkpoint 记录 message/event bounds 与 opaque artifact handles。
- 负责：持久化任务预算快照与累计使用量，使恢复同一 thread 不重置限制。
- 负责：提供事务边界和稳定 ID，支持 TUI 与非交互 CLI 共享同一会话。
- 不负责：执行回合、调用模型、运行命令、决定权限或保存明文密钥。
- 不负责：把模型摘要作为唯一的任务状态来源。
- 预算预留与读取必须保留 token、repair、failure、active-time 及 warning 等全部累计字段；恢复不得重置扩展计数。
- 负责原子记录运行实例和中断 checkpoint，并只对已失效 owner 的运行任务执行幂等恢复对账。
- 负责持久化 contract revision、code generation、verification run、append-only evidence 与原子 completion；完成事务必须复核最新 generation 和全部 required evidence。
- 负责记录创建任务时的 profile 身份、模型、协议和脱敏 endpoint host，并支持旧会话库的可重复、非破坏性迁移。
- 不负责解析 verifier 输出、执行验证或把模型消息提升为 evidence。

## Units
- `ThreadStatus`、`GoalStatus`、`ThreadSummary`、`GoalRecord`、`CheckpointRecord`: 表达不可变的会话、目标和 checkpoint 状态 | 无副作用 | 时间统一归一化为 UTC，元数据深度冻结
- `SQLiteSessionRepository`: 实现内核会话协议并提供线程列表、归档与事件恢复读取 | SQLite I/O | 每个异步操作使用独立的短事务连接
- `SQLiteSessionRepository.load_task_state`、`save_task_state`、`reduce_task_state`: 读取、保存并在单事务中归约有界任务事实 | SQLite I/O | 可验证事实与模型工作笔记分离，笔记始终作为未验证内容；缺失状态返回空状态，缺失线程失败闭合
- `SQLiteSessionRepository.get_or_create_task_budget`、`reserve_task_budget`: 创建、读取并原子保留模型回合和工具调用额度 | SQLite I/O | 同一 thread 的预算快照不可被恢复操作重置
- `SQLiteSessionRepository.create_task`、`load_task`、`transition_task`、`list_tasks`: 持久化任务契约和生命周期 | SQLite I/O | thread 是会话容器，task 是可恢复执行单元
- `consume_task_usage`、`observe_task_validation`、`record_task_active_seconds`: 原子累计 token、失败指纹和活跃时间 | SQLite I/O | 恢复同一任务不能重置预算或卡滞计数
- `record_task_control`、`consume_task_controls`: 有序保存并在安全边界原子消费 steering | SQLite I/O | 已消费指令绝不在恢复时重放
- `register_task_execution`、`reconcile_stale_tasks`: 记录任务 owner 身份并原子中断失效 owner 的活动任务 | SQLite I/O | 调用方提供 PID/create_time 身份判定，活 owner 不得被接管
- `save_task_contract_revision`、`begin_verification_run`、`append_verification_evidence`: 保存 revision 与 append-only verification ledger | SQLite I/O | 未结束 run 不能被当作成功 evidence
- `finalize_task`、`interrupt_open_verification_runs`: 在一个事务内复核当前 revision/run/required evidence 后完成，或把恢复前在途 run 标为 interrupted | SQLite I/O | 不存在通用的 evidence-free completed 路径
- `RecordRepositoryMixin`: 保存、更新和读取目标与 checkpoint | SQLite I/O | 所有记录必须归属于存在的线程
- `SessionDatabase`: 执行版本化 schema migration、连接配置、事务和完整性校验 | SQLite I/O | 未来版本、缺表和损坏数据均失败闭合
- `migrate_legacy_session_database`: 使用 SQLite backup API 复制旧库并校验计数与完整性 | SQLite I/O | 临时目标原子替换，旧库始终保留
- `encode_message`、`decode_message`、`encode_event`、`decode_event`: 在核心模型与稳定 JSON 记录间转换 | JSON 编解码 | 不能信任的持久化内容抛出专用损坏错误
