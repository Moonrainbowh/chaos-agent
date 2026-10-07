# Sessions
把聊天、目标、动作、用量和 checkpoint 保存为可恢复、可迁移的结构化状态。

## 边界
- S12审批/待决定账本：Host预览/摘要绑定根/状态/owner/TTL，CAS决定；漂移/过期留stale/expired，不执行/授予权限。
- S10：提供行数/字节双有界历史页、稳定旧ID检索、SQL数量/高水位/未决动作与增量进展CAS；原日志不变，同序号修改以触发器revision使恢复决定与进展缓存失效。全量接口仅保留显式兼容。
- conversation Provider 用量按 MODEL_STARTED 分组、组内最后有效 Usage snapshot 聚合；SQL 读取日志事实但不物化消息树/正文，Python 仅取汇总、最新 Usage 和有界 models，不把预算租约投影为 Provider 用量。
- 未配对动作是持久恢复闸门；解决决定以原消息sequence和具体task/thread/call进行事务CAS，拒错任务、过期、重复及未决状态漂移。保存理由/证据与配对回执同事务，不删除原记录、重置预算/工作区或伪造验证通过。
- 消息分支索引保存原节点、分叉锚点/书签；分叉复制工具配对完整前缀，新任务不继承授权/验证，与两层Agent关系独立。
- 为 persistent context 提供任务内按路径笔记，覆盖/追加原子执行，保留版本和幂等操作记录；不改变原始消息或工作区文件。
- Message JSON保存附件引用元数据，恢复/fork/checkpoint/rewind引用不变；兼容缺附件字段的旧消息。
- 不保存附件blob/base64/原绝对路径，不解析或修复附件。
- 原子持久化 lineage、snapshot manifest、checkpoint 游标与 rewind operation，并提供非破坏性任务分叉及 session Rewind 终态事务。
- 线程生命周期、消息与事件存储、持久目标、checkpoint 元数据、恢复和 schema migration。
- 会话稳定分页、消息序号历史页；延续thread原子继承消息，不复制任务、用量、事件、验证或授权。
- checkpoint 记录 message/event bounds 与 opaque artifact handles。
- 持久化任务预算快照与累计使用量，使恢复同一 thread 不重置限制。
- 父子预算绑定冻结任务、owner、委派请求和局部上限；工具/模型计数与共享 token 请求在同一数据库事务准入。实际 Provider 用量完整保存，partial/unknown 保留预留负债，重启不赠回额度。
- 绑定子执行的真实写入、部分编辑与实际命令尝试，以动作回执在同事务投影父 TaskState 并递增 generation、清空旧 subject；同动作重放幂等，异实例拒绝。子建议及验证声明不成为父 evidence；父快照不得覆盖并发子变更。
- `save_task_state_if_current` 对文件快照前读取的完整 TaskState 做事务 CAS，拒绝并发漂移且不改变持久事实；普通历史/恢复保存接口不增加该前置要求。
- 提供事务边界和稳定 ID，支持 TUI 与非交互 CLI 共享同一会话。
- 不执行回合/模型/命令/Git、读取文件/恢复工作树、决定权限/授权或保存明文密钥；模型摘要不能是唯一任务状态来源。
- 预算预留与读取必须保留 token、repair、failure、active-time 及 warning 等全部累计字段；恢复不得重置扩展计数。
- 软预算租约的 tier、当前阈值、续约次数、最终延伸、可信进展基线和最近原因必须与累计用量同事务持久化；checkpoint、fork 与 rewind 不能重置租约或再次取得已经使用的延伸。
- 运行实例/中断checkpoint原子保存；仅失效owner的运行任务幂等恢复对账。begin同事务先核owner再按TaskRecord激活；拒绝不改事实，相同owner重试不恢复paused、不改updated_at。旧注册只收active且不覆盖owner；相同instance/PID/创建时间幂等、保started_at；其他owner先显式释放/对账，释放按instance CAS。
- 持久化 contract revision、code generation、verification run、append-only evidence 与原子 completion；完成事务必须复核最新 generation 和全部 required evidence。
- 记录创建任务时的 profile 身份、模型、协议和脱敏 endpoint host，并支持旧会话库的可重复、非破坏性迁移。
- Windows迁移临时文件使用Win32卷序列号/文件索引，兼容Python3.13的64位`st_dev`，防路径替换校验不变。
- 原消息获thread内稳定单调序号，按序号读取不可变记录。
- 持久化最多两级的 `parent_thread_id`；只有根 thread 可以创建直接子 thread，Workflow edge 不参与授权。
- 在单一事务中发布语义 checkpoint 及其来源索引，使恢复、搜索和读取只看到完整 generation。
- 运行中follow-up独立持久排队；仅Core安全收尾门单事务按序提升为messages，暂停/中断/重启不丢未提升项。
- 持久化 Workflow、节点、边和 Host 观察结果，并支持启动恢复时的幂等对账。
- 持久化 thread 级 Skill 激活身份、来源和 digest；不保存完整 Skill 文本。
- 不解析verifier/执行验证/提升模型消息为evidence，不计算thread读权限、生成摘要、解释Workflow或执行Skill/MCP。
- 以 v18 专用表保存本机同用户打开实例的 PID+创建时间、心跳、稳定 ref 与纯文本 PEER 收件箱，并提供有界去重、限流、队列、过期和 CAS claim/ack。
- 不负责：把 peer 正文写入普通 `messages`、`task_controls`，或把它提升为用户授权、steering、斜杠命令和跨机器传输。

- 以专用表持久化 workspace coverage、prepared/completed mutation、path preimage、代码 owner scope 和 checkpoint mutation 高水位，并提供同事务的有界 rewind observation。
- 不负责：解析 SnapshotHandle、读取工作区、判断当前路径冲突或把 checkpoint metadata 当作可信 rewind 事实。
- v19 companion表保存批准编辑批次、操作端点existence/hash/size及进度，与parent mutation原子闭合终态。
- 不负责：读取或修改工作区、判定用户漂移、执行回滚或启动恢复；这些只消费 Sessions 中的持久事实。

### 预算框架（显式启用的 v1 已实现）

- ContextJournal 提供窗口 CAS、幂等笔记/请求及原子调用预算预留/结算。原始消息保持不变；内部 context: 标签不进入普通工作区检查点列表；旧任务消耗与冻结额度不会因换窗或重启归零。
- 配置/实验边界见`docs/context-boundary-experiment.md`、`docs/context-boundary-results.md`；候选值可配，实验不自动改变默认策略。

## Units
- S16：必要来源随既有 `context:child_budget` 冻结；旧记录缺字段等价空，有来源的记录不能以空或冲突集合覆盖。`source_completion_state`、`append_source_correction` 使用既有 checkpoints/messages；纠正 completed baseline 与 developer notices 同事务落地，CAS拒绝旧边界，不重置预算或建立子 TaskRecord。
- `ApprovalRepositoryMixin`：v26卡CAS | SQLite I/O | 8KiB/100条/TTL≤1h；待决定只收无owner暂停/中断/等待；同响应绑定有效才幂等，consumed_now区分首次；等待决定可同事务显式failed/accepted_partial。
- `HistoryQuery/Display/ContextRepositoryMixin`：双有界页、旧UUID原文片段、Unicode检索、窗口/用量聚合和进展cursor CAS；v25触发器保配对索引与恢复revision，旧库一次分页回填。
- recovery_checklist/resolve_pending_action/recovery_mutation_receipt：事务版本核对与绑定动作回执；决定/反馈同事务追加，可信本地文件核验由Host完成。
- `ConversationTreeRepositoryMixin`：v24 原子维护 canonical message node、前缀分叉、共享历史引用、书签与会话组事件 | SQLite I/O | 与两层 Agent 授权关系独立；分叉不复制任务、事件或验证事实，工具配对必须闭合。
- `OwnedTemporary`（共享临时文件助手）：POSIX按设备/inode及可用birthtime清理，写入/硬链接更新ctime不算替换；close仅释放保活描述符，身份捕获失败仍关闭并清理已知归属临时文件 | 文件I/O | 不删异身份替代文件；Windows用Win32校验
- `ContextNotesRepositoryMixin`: 笔记覆盖/追加单事务、幂等工具请求、版本保留 | SQLite I/O | 虚拟相对路径、单文件 1MB；内部 context:note_file 记录不作为工作区恢复点
- `ThreadStatus`、`GoalStatus`、`ThreadSummary`、`ThreadRelation`、`MessageRecord`、`GoalRecord`、`CheckpointRecord`: 表达不可变的会话、父子关系和 checkpoint 状态 | 无副作用 | 时间归一化为 UTC，元数据深度冻结
- `WorkspaceLineageRecord`、`WorkspaceSnapshotRecord`、`CheckpointCursor`、`RewindOperationRecord`: 表达 lineage、manifest、会话游标与 Rewind 状态 | 无副作用 | UUID、枚举、绝对路径、摘要、时间、JSON 与容量均严格校验
- `SQLiteSessionRepository`、`record_task_steering(...)`、`record_task_followup(...)`、`promote_task_followups(...)`: 组合短事务仓储，分别原子持久立即 steering 和隐藏于当前回合的 FIFO follow-up | SQLite I/O | follow-up 只能在 Core 收尾门一次性提升为 messages，附件不得成为孤立记录
- `ThreadContentRepositoryMixin`、`list_threads(...)`、`load_message_records(...)`、`create_thread_from_history(...)`: 稳定分页、排他序号历史页、事务消息延续 | SQLite I/O | 列表页1..1000、offset不限总量；消息按序号升序，默认读全部；延续挂原根，继承标题/消息payload/时间并分配新序号，不复制任务、授权、事件、目标、预算或验证
- `RecordRepositoryMixin`、`SemanticRepositoryMixin`: 原子保存目标、普通/语义 checkpoint 与来源索引 | SQLite I/O | 稳定 ID 内容漂移、缺失 owner 与损坏 JSON 均失败闭合
- `WorkflowRepositoryMixin`、`SkillActivationRepositoryMixin`: 保存已校验 DAG 与 thread-scoped Skill 身份 | SQLite I/O | Workflow thread 限于两级树；Skill 不保存正文且 upsert 不重复
- `WorkspaceSnapshotRepositoryMixin`: 创建/读取 lineage，并在一个写事务中发布 snapshot entries、checkpoint 与 cursor | SQLite I/O | blob 仅作摘要元数据；任一写入失败完全回滚，available/unavailable 关联必须一致
- `RewindRepositoryMixin`: 以 CAS 开始、完成或失败 Rewind，并按 lineage/时间查询待恢复操作 | SQLite I/O | 状态机幂等；跨 lineage checkpoint、非法 completion 与 recovery-required 后续写入失败闭合
- `EditBatchRepositoryMixin`、`record_edit_batch_post_identities(...)`: 准备、查询、迁移、记录操作进度与 POST 所有权证明并闭合多文件编辑批次 | SQLite I/O | 同 workspace 最多一个 unresolved；计划漂移、逆序进度、相反终态与冲突码漂移均失败闭合；`target_post_device/inode`（v22）仅APPLYING写入：替换后索引在journal落库时尚不存在；POST证明不参与EditBatchPath相等性，避免prepare重放误判漂移
- `CheckpointForkRepositoryMixin`、`AtomicSessionRewindRepositoryMixin`: 从 checkpoint 非破坏性分叉游标前事实与累计预算；以单事务创建 paused replacement、转交 owner、SUPERSEDE 旧任务并完成 operation | SQLite I/O | session/combined 禁止 generic completion；任一写入故障整体回滚为 pending，completed 幂等重试复核 source/replacement/owner/lineage/status 事实
- `save_task_contract_revision`、`begin_verification_run`、`append_verification_evidence`、`finalize_task`: 保存 append-only 验证账本并原子完成 | SQLite I/O | 必须复核最新 generation、revision 与全部 required evidence
- `SessionDatabase/migrate_legacy_session_database/JSON codecs`：v1-v26迁移、schema/index/FK/历史revision触发器校验、旧库复制及Message附件引用编解码 | SQLite/JSON I/O | v20follow-up/v21记忆/v22POST身份/v23软租约/v24消息树/v25历史投影/v26审批；未来版本/损坏失败闭合，保留旧库。
- `get_or_create_task_budget(...)`、`reserve_task_budget(...)`: 创建冻结的 quick/standard/deep 软租约，并在单个 `BEGIN IMMEDIATE` 事务内按硬上限、可信进展基线和续约资格预留额度 | SQLite I/O | 类型化区分普通预留、续约、软租约耗尽和硬上限耗尽；相同快照不能重复续约
- `budget_payload(...)`、`copy_budget(...)`: 将租约状态写入 checkpoint，并在 fork/rewind 时采用不可回退的累计使用量和当前 owner 的权威租约元数据 | SQLite/JSON I/O | lineage 只累计用量，不建立第二套租约状态机
- `PeerSessionRepositoryMixin`、`PeerMessageRepositoryMixin`、`PeerInboxRepositoryMixin`: 原子注册/心跳/rename 本机实例并保存、领取/续租/查询显式 `peer` origin 纯文本 | SQLite I/O | v18；同名允许但 ref 唯一；held 与 claim lease 分离，过期 lease可恢复，closed 与消息终态不可回退
- `ContextJournalRepositoryMixin`、`bind_child_budget`: 原子追加窗口/笔记/请求预算、冻结父子归属及局部上限 | SQLite I/O | 共享 owner 准入；delegate ID 不重复创建，child 次数重启保留；partial/unknown 不释放负债，settle 单次投影真实费用且保留超限事实；不覆盖原始消息
- `MemoryRepositoryMixin`、`MemoryRecord`: 按scope/revision保存记忆；scoped读改删、CAS保留未提供条件/来源，forget屏障阻复活 | SQLite I/O | 检索先scope/最新active/适用条件再词法排序分页，行/字节有界；不改变权限或任务事实
- `MemoryRepositoryMixin.search_memory_diagnostics(...)`: 与search同SQL条件/排序，输出scope、候选/排除计数和选中ID | SQLite只读 | require_applicable仅无条件或条件全匹配；不泄漏受限正文
- `assess_memory_applicability(record, context)`: 相对于当前任务事实计算适用性，不改变记忆 lifecycle | 无副作用 | 条件缺失为 needs_check，已撤回/替代/归档为 not_applicable
- `MemoryRepositoryMixin.promote_memory(...)`: 在显式 user-scope 授权下，以调用方提供的去项目化正文创建独立 user 条目并保留 derived_from | SQLite I/O | 不复制原项目正文，不开放原始来源权限
- `RecoveryRepositoryMixin.recovery_checklist(task_id)`: 任务、消息/事件、最新checkpoint游标、Notes revision、follow-up队列、owner、未配对调用和验证账本的有界清单 | SQLite 只读 | 未配对调用标为 unknown，禁止恢复时直接重放；工作区漂移仍由 workspace/verification 服务核对

- `context:` 为内部日志保留标签；普通检查点创建拒绝该命名空间，列表隐藏内部窗口/笔记/用量，防止工作区恢复误选。

- POSIX 临时文件持有描述符至清理结束，防止删除后 inode 复用把外来替换文件误认为原文件；ctime 不作为创建身份。
