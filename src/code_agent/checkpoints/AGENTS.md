# Checkpoints
协调 durable checkpoint 的工作区捕获、预览、三模式 Rewind 与崩溃恢复。

## 边界
- 负责：在命令树已终止且持有 lineage lease 时协调 Workspace 与 Sessions 创建可恢复 checkpoint。
- 负责：提供仅代码、仅会话、代码与会话三种非破坏性 Rewind，并以 pre-rewind checkpoint 和持久 intent 实现故障回滚。
- 负责：恢复前生成有界预览、要求显式确认并复验工作区 fingerprint。
- 不负责：实现 Git、路径防护、blob 存储、SQLite 细节、终端渲染或模型调用。
- 不负责：重放命令、删除旧历史、重置预算、修改 source worktree 或把多文件恢复宣称为原子事务。

## Units
- `RewindPreview`、`RewindResult`: 表达有界预览、显式 operation identity 与最终 Saga 结果 | 无副作用 | 路径最多 100 条且总展示字节有界
- `CheckpointSessionsPort`、`CheckpointWorkspacePort`、`LineageLocks`: 约束持久事实、快照字节与逐 lineage 互斥边界 | 具体副作用由实现负责 | Workspace I/O 不得被静默降级
- `LineageLockPool.for_lineage(lineage_id)`: 串行化同一 lineage 的 capture、preview、execute 与 recovery | 持有进程内异步锁
- `CheckpointService.capture(task_id, label)`: 等待命令树终止后捕获 inventory、snapshot/blob 并原子发布 checkpoint/cursor | 写 blob、Sessions 与审计事件 | 只有明确容量/时限上限可发布 session-only checkpoint
- `CheckpointService._capture_locked(...)`: 为已持 lineage 锁的 Saga 创建 pre-rewind checkpoint | 与 `capture` 相同 | 不再次取锁或 quiesce，避免重入死锁
- `RewindCoordinator.preview(...)`: 在 owner/recovery guard 和 lineage 锁内生成有界三模式预览 | 读取 Workspace 与 Sessions | 代码模式要求完整可物化 blob；issued preview 防篡改和重放
- `RewindCoordinator.execute(...)`: 仅接受字面量 `confirmed is True`，持锁复验 fingerprint 后按模式执行并显式补偿 | 可恢复代码、分叉会话、转交 owner、使旧任务 `SUPERSEDED` | combined 必须先校验代码再分叉会话
- `RewindRecovery`: 回滚失败操作并逐 lineage 对账 pending intent | 恢复 rollback checkpoint、记录 rolled_back/recovery_required、触发 guard 与 invalidation | 永不 forward resume 或重放命令
