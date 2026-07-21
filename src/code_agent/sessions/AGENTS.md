# Sessions
把聊天、目标、动作、用量和 checkpoint 保存为可恢复、可迁移的结构化状态。

## 边界
- 负责：线程生命周期、消息与事件存储、持久目标、checkpoint 元数据、恢复和 schema migration。
- 负责：持久化任务预算快照与累计使用量，使恢复同一 thread 不重置限制。
- 负责：提供事务边界和稳定 ID，支持 TUI 与非交互 CLI 共享同一会话。
- 不负责：执行回合、调用模型、运行命令、决定权限或保存明文密钥。
- 不负责：把模型摘要作为唯一的任务状态来源。
- 预算预留与读取必须保留 token、repair、failure、active-time 及 warning 等全部累计字段；恢复不得重置扩展计数。
- 负责原子记录运行实例和中断 checkpoint，并只对已失效 owner 的运行任务执行幂等恢复对账。
- 负责持久化 contract revision、code generation、verification run、append-only evidence 与原子 completion；完成事务必须复核最新 generation 和全部 required evidence。
- 负责记录创建任务时的 profile 身份、模型、协议和脱敏 endpoint host，并支持旧会话库的可重复、非破坏性迁移。
- 负责：为每条原始消息分配 thread 内稳定、单调的序号，并按序号读取不可变消息记录。
- 负责：持久化最多两级的 `parent_thread_id`；只有根 thread 可以创建直接子 thread，Workflow edge 不参与授权。
- 负责：在单一事务中发布语义 checkpoint 及其来源索引，使恢复、搜索和读取只看到完整 generation。
- 负责：持久化 Workflow、节点、边和 Host 观察结果，并支持启动恢复时的幂等对账。
- 负责：持久化 thread 级 Skill 激活身份、来源和 digest；不保存完整 Skill 文本。
- 不负责解析 verifier 输出、执行验证或把模型消息提升为 evidence。
- 不负责：计算 thread 读取权限、生成语义摘要、解释 Workflow 状态或执行 Skill/MCP。

## Units
- `ThreadStatus`、`GoalStatus`、`ThreadSummary`、`GoalRecord`、`CheckpointRecord`: 表达不可变的会话、目标和 checkpoint 状态 | 无副作用 | 时间统一归一化为 UTC，元数据深度冻结
- `ThreadRelation`、`MessageRecord`: 表达最多两级的父子关系与带稳定数据库序号的原始消息 | 无副作用 | Workflow edge 不参与授权，原消息不被摘要覆盖
- `SQLiteSessionRepository.create_thread`、`load_thread_relation`、`load_message_records`: 原子创建两级 thread 并按稳定序号读取关系和消息 | SQLite I/O | 子 thread 不得再创建子 thread，缺失父 thread 失败闭合
- `SemanticRepositoryMixin.publish_semantic_checkpoint`、`load_semantic_checkpoints`、`search_thread_index`、`read_thread_entry`: 原子发布并读取可重建的 semantic checkpoint 与来源索引 | SQLite I/O | 稳定 ID 内容漂移失败闭合，不使用 pickle
- `WorkflowRepositoryMixin.save_workflow_snapshot`、`load_workflow_snapshot`、`load_workflow_for_task`: 原子保存并恢复已校验 DAG 快照 | SQLite I/O | assigned thread 必须位于根 thread 两级树内，持久化前重新校验环和端点
- `SkillActivationRepositoryMixin`: 保存、列出和移除 thread-scoped Skill ID/source/digest | SQLite I/O | 不保存完整 Skill 文本，upsert 不产生重复激活
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
