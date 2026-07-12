# Sessions
把聊天、目标、动作、用量和 checkpoint 保存为可恢复、可迁移的结构化状态。

## 边界
- 负责：线程生命周期、消息与事件存储、持久目标、checkpoint 元数据、恢复和 schema migration。
- 负责：持久化任务预算快照与累计使用量，使恢复同一 thread 不重置限制。
- 负责：提供事务边界和稳定 ID，支持 TUI 与非交互 CLI 共享同一会话。
- 不负责：执行回合、调用模型、运行命令、决定权限或保存明文密钥。
- 不负责：把模型摘要作为唯一的任务状态来源。

## Units
- `ThreadStatus`、`GoalStatus`、`ThreadSummary`、`GoalRecord`、`CheckpointRecord`: 表达不可变的会话、目标和 checkpoint 状态 | 无副作用 | 时间统一归一化为 UTC，元数据深度冻结
- `SQLiteSessionRepository`: 实现内核会话协议并提供线程列表、归档与事件恢复读取 | SQLite I/O | 每个异步操作使用独立的短事务连接
- `SQLiteSessionRepository.load_task_state`、`save_task_state`、`reduce_task_state`: 读取、保存并在单事务中归约有界任务事实 | SQLite I/O | 可验证事实与模型工作笔记分离，笔记始终作为未验证内容；缺失状态返回空状态，缺失线程失败闭合
- `SQLiteSessionRepository.get_or_create_task_budget`、`reserve_task_budget`: 创建、读取并原子保留模型回合和工具调用额度 | SQLite I/O | 同一 thread 的预算快照不可被恢复操作重置
- `SQLiteSessionRepository.create_task`、`load_task`、`transition_task`、`list_tasks`: 持久化任务契约和生命周期 | SQLite I/O | thread 是会话容器，task 是可恢复执行单元
- `consume_task_usage`、`record_task_control`: 原子累计 token/重试/失败签名并保存控制指令 | SQLite I/O | 恢复同一任务不能重置预算或丢失 steering
- `RecordRepositoryMixin`: 保存、更新和读取目标与 checkpoint | SQLite I/O | 所有记录必须归属于存在的线程
- `SessionDatabase`: 执行版本化 schema migration、连接配置、事务和完整性校验 | SQLite I/O | 未来版本、缺表和损坏数据均失败闭合
- `encode_message`、`decode_message`、`encode_event`、`decode_event`: 在核心模型与稳定 JSON 记录间转换 | JSON 编解码 | 不能信任的持久化内容抛出专用损坏错误
