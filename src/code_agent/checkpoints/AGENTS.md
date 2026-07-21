# Checkpoints
协调 durable checkpoint 的工作区捕获、预览、三模式 Rewind 与崩溃恢复。

## 边界
- 负责：在命令树已终止且持有 lineage lease 时协调 Workspace 与 Sessions 创建可恢复 checkpoint。
- 负责：提供仅代码、仅会话、代码与会话三种非破坏性 Rewind，并以 pre-rewind checkpoint 和持久 intent 实现故障回滚。
- 负责：恢复前生成有界预览、要求显式确认并复验工作区 fingerprint。
- 不负责：实现 Git、路径防护、blob 存储、SQLite 细节、终端渲染或模型调用。
- 不负责：重放命令、删除旧历史、重置预算、修改 source worktree 或把多文件恢复宣称为原子事务。

## Units
