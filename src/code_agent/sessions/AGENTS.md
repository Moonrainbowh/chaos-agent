# Sessions
把聊天、目标、动作、用量和 checkpoint 保存为可恢复、可迁移的结构化状态。

## 边界
- 负责：原子持久化 lineage、snapshot manifest、checkpoint 游标与 rewind operation，并提供非破坏性任务分叉及 session Rewind 终态事务。
- 负责：线程生命周期、消息与事件存储、持久目标、checkpoint 元数据、恢复和 schema migration。
- 负责：持久化任务预算快照与累计使用量，使恢复同一 thread 不重置限制。
- 负责：提供事务边界和稳定 ID，支持 TUI 与非交互 CLI 共享同一会话。
- 不负责：执行回合、调用模型、运行命令、决定权限或保存明文密钥。
- 不负责：读取文件、执行 Git、恢复工作树或决定用户授权。
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
- `ThreadStatus`、`GoalStatus`、`ThreadSummary`、`ThreadRelation`、`MessageRecord`、`GoalRecord`、`CheckpointRecord`: 表达不可变的会话、父子关系和 checkpoint 状态 | 无副作用 | 时间归一化为 UTC，元数据深度冻结
- `WorkspaceLineageRecord`、`WorkspaceSnapshotRecord`、`CheckpointCursor`、`RewindOperationRecord`: 表达 lineage、manifest、会话游标与 Rewind 状态 | 无副作用 | UUID、枚举、绝对路径、摘要、时间、JSON 与容量均严格校验
- `SQLiteSessionRepository`: 组合短事务仓储，持久化 thread、消息/事件、task、任务状态、预算、控制和运行 owner | SQLite I/O | token/tool 等累计 lineage 用量不能通过恢复重置；failure pair 以 current bound owner budget 为准，无可信来源的 legacy pair 归零
- `RecordRepositoryMixin`、`SemanticRepositoryMixin`: 原子保存目标、普通/语义 checkpoint 与来源索引 | SQLite I/O | 稳定 ID 内容漂移、缺失 owner 与损坏 JSON 均失败闭合
- `WorkflowRepositoryMixin`、`SkillActivationRepositoryMixin`: 保存已校验 DAG 与 thread-scoped Skill 身份 | SQLite I/O | Workflow thread 限于两级树；Skill 不保存正文且 upsert 不重复
- `WorkspaceSnapshotRepositoryMixin`: 创建/读取 lineage，并在一个写事务中发布 snapshot entries、checkpoint 与 cursor | SQLite I/O | blob 仅作摘要元数据；任一写入失败完全回滚，available/unavailable 关联必须一致
- `RewindRepositoryMixin`: 以 CAS 开始、完成或失败 Rewind，并按 lineage/时间查询待恢复操作 | SQLite I/O | 状态机幂等；跨 lineage checkpoint、非法 completion 与 recovery-required 后续写入失败闭合
- `CheckpointForkRepositoryMixin`、`AtomicSessionRewindRepositoryMixin`: 从 checkpoint 非破坏性分叉游标前事实与累计预算；以单事务创建 paused replacement、转交 owner、SUPERSEDE 旧任务并完成 operation | SQLite I/O | session/combined 禁止 generic completion；任一写入故障整体回滚为 pending，completed 幂等重试复核 source/replacement/owner/lineage/status 事实
- `save_task_contract_revision`、`begin_verification_run`、`append_verification_evidence`、`finalize_task`: 保存 append-only 验证账本并原子完成 | SQLite I/O | 必须复核最新 generation、revision 与全部 required evidence
- `SessionDatabase`、`migrate_legacy_session_database`、稳定 JSON codecs: 执行 v1-v15 migration、schema/index/FK 校验、旧库复制与记录编解码 | SQLite/JSON I/O | 未来版本、缺表/索引、损坏数据失败闭合；旧库始终保留
