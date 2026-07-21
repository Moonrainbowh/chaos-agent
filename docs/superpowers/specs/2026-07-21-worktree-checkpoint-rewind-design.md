# Worktree Checkpoint Rewind 设计

## 状态

已于 2026-07-21 经用户批准。作为独立批次在流式 TUI 之后交付。

## 目标

- 每个前台任务在独立 Git worktree 中执行，主工作区不被 Agent 或 Rewind 修改。
- 每个 durable checkpoint 关联可持久恢复的工作区代码快照。
- 提供“仅代码”“仅会话/任务状态”“代码与会话/任务状态”三种 Rewind。
- 默认选择“仅代码”，恢复前展示 checkpoint、文件统计与风险并要求确认。
- 中断、失败或进程崩溃后不重放未完成命令，并能恢复或回滚未完成的 Rewind。

## 非目标

- 不捕获 `.gitignore` 忽略项、`.env`、密钥、构建缓存、依赖目录或 `.git` 元数据。
- 不修改、清理或 reset 用户主工作区。
- 不删除历史消息、事件、checkpoint、审计记录或旧任务。
- 不把多文件文件系统恢复描述为底层原子事务。
- 不自动 commit、push、合并到主分支或删除仍被引用的 worktree。
- 不在首版支持非 Git 工作区的代码 Rewind；其现有会话恢复继续可用。

## 当前基线

- `WorkspaceEditor.snapshot(...)` 和 `WorkspaceSnapshot.restore(...)` 已支持受路径保护的内存字节快照与逐文件原子替换。
- Snapshot 仅覆盖调用方列出的路径，没有持久 manifest、内容去重、完整工作树枚举或应用层调用。
- Checkpoint 当前只保存 label 与 JSON metadata。
- 前台任务绑定原工作区路径，没有 worktree lease。
- Runtime、Verification、Workspace tools 和上下文扫描均由根目录组合时注入同一个 workspace root。

## 设计决策

### Workspace Lineage 与任务 worktree

新增 Host-neutral `WorkspaceLineage` 概念：

- `source_root`：用户启动 Chaos Agent 的主工作区，只作为播种来源和最终人工集成目标。
- `worktree_root`：任务实际工作目录。
- `repository_id`：由 Git common dir 的规范路径生成稳定摘要。
- `lineage_id`：一次任务/Rewind 分支共享的工作区谱系。
- `owner_task_id`：当前拥有写 lease 的任务。
- `head_commit`、`branch_name`、创建和最后使用时间。

任务启动流程：

1. 验证 source root 是 Git worktree，读取 common dir、HEAD 和状态。
2. 在 `%LOCALAPPDATA%\chaos-agent\worktrees\<repository-id>\<lineage-id>` 创建受管 worktree 与 `codex/task-<short-id>` 风格分支。
3. 枚举主工作区中 Git tracked 与未忽略 untracked 的合规文件。
4. 以 `WorkspaceSnapshot` 把主工作区当前内容播种到任务 worktree；不复制 `.git`、ignored 或敏感路径。
5. 根级组合使用 `worktree_root` 构造 Guard、WorkspaceFiles、Editor、Git、Runtime、Verification、Context Builder 和 Dispatcher。
6. 成功持久化 lineage 与 lease 后任务才能运行。

同一 lineage 同时只有一个写 owner。旧任务仍可读历史，但不能继续操作已转交的 worktree。

### 快照捕获边界

Checkpoint 捕获：

- Git tracked 文件，包括 checkpoint 时已删除的路径状态。
- Git 未忽略、通过 `WorkspacePathGuard` 与敏感文件策略的 untracked 普通文件。
- 必要的文件 mode 元数据；首版不恢复 ACL、所有者、ADS 或时间戳。

排除：

- 任意层级 `.git`、`.chaos-agent`、`.code-agent`。
- ignore rules 命中的路径。
- `.env`、私钥和现有敏感文件规则命中的路径。
- symlink、junction、reparse point 与越界目标。
- 超出单文件、总字节、文件数或扫描 deadline 的快照。

捕获超限时 checkpoint 的会话部分仍可创建，但必须明确标记 `workspace_snapshot_status=unavailable`，不能向用户声称可恢复代码。

### 内容寻址持久化

原始字节不写入 checkpoint JSON 或重复塞入 SQLite：

```text
%LOCALAPPDATA%\chaos-agent\snapshots\blobs\aa\<sha256>
```

- blob 名称等于内容 SHA-256，使用同目录临时文件、`fsync` 和原子 rename 发布。
- 相同内容跨 checkpoint 去重。
- SQLite 保存 snapshot、entry、blob hash、大小、mode、存在性和 inventory digest。
- Manifest 与 checkpoint 关联在同一数据库事务中变为可见。
- 写成但未被数据库引用的 blob 是安全 orphan，由有界 GC 延迟清理。
- 引用计数由数据库事实计算，不依赖可漂移的文件旁路计数器。

Sessions schema 新增：

- `workspace_lineages`
- `workspace_snapshots`
- `workspace_snapshot_entries`
- `rewind_operations`
- checkpoint 到 snapshot 的可空外键或关联表

`CheckpointRecord.metadata` 只保存稳定 ID 和可展示摘要，不内嵌 manifest。

### Checkpoint 创建

所有可恢复 checkpoint 通过应用服务统一创建：

1. 请求任务暂停并等待 Runtime 确认命令进程树终止。
2. 获取 lineage 写 lease 和 workspace lock。
3. 枚举合规 inventory，并调用 Workspace snapshot 能力复制原始字节。
4. 原子发布缺失 blob，计算 manifest/inventory digest。
5. 在一个 SQLite 事务中写 workspace snapshot、entries、checkpoint、任务/预算/消息/事件游标。
6. 提交后发布 `TASK_CHECKPOINT_CREATED` 事件。

Checkpoint 不保存或重放 in-flight 命令。自动 checkpoint 至少覆盖 task-created、paused、interrupted、pre-rewind 和用户显式创建边界。

### Rewind 预览

TUI/CLI 选择 checkpoint 后读取 manifest 与当前 inventory，生成：

- 将恢复、删除、重建的文件数量与总字节。
- 有界路径列表和 Git 风格差异摘要。
- workspace snapshot 是否完整、是否超限或缺 blob。
- 选定模式及其对任务/会话的影响。
- 当前 workspace fingerprint，用于确认后的并发复验。

确认默认 No。代码不可恢复的 checkpoint 禁用代码相关模式，但仍允许仅会话模式。

### Rewind Saga

数据库与多文件恢复不能成为同一原子事务，因此新增 `RewindCoordinator`：

1. 暂停任务、终止命令树并获取独占 lease。
2. 生成预览并获得用户确认。
3. 在当前状态自动创建完整 `pre-rewind` checkpoint，作为撤销和故障回滚点。
4. 写入 `rewind_operations(status=pending, source_checkpoint, rollback_checkpoint, mode, fingerprint)`。
5. 复验 workspace fingerprint；变化则安全拒绝并要求重新预览。
6. 按所选模式执行代码恢复、会话分叉或二者。
7. 校验最终 inventory digest，转交 worktree lease，追加审计事件并标记 operation completed。

代码恢复使用现有 `WorkspaceSnapshot.restore(...)`：

- Manifest 中存在的文件恢复其原始字节。
- 当前 inventory 比 manifest 多出的合规文件转换为 `existed=False` entry 并删除。
- 恢复前先完成所有路径、父目录、blob、容量和权限预检。
- 单文件仍采用同目录原子 replace；多文件不声称原子。

任一步失败时立即使用 pre-rewind snapshot 回滚。进程崩溃后，启动对账必须先处理 pending operation，完成回滚后才允许该 lineage 执行新命令。若回滚也失败，lineage 标记 `recovery_required`，所有写入和命令失败闭合，并向用户展示缺失路径与人工恢复指引。

### 三种模式

#### 仅代码（默认）

- 在当前 lineage/worktree 恢复代码。
- 当前任务和会话事实不回退。
- 追加 rewind 审计事件并使旧 Verification Evidence 失效。
- 任务恢复到可继续状态，由用户下一条指令决定后续工作。

#### 仅会话/任务状态

- 不修改 worktree 内容。
- 从 checkpoint 游标复制必要消息、目标、任务状态和累计预算，创建新的任务分支记录。
- 新任务继承同一 lineage，并在原任务停止后取得写 lease。
- `TaskStatus` 新增终态 `SUPERSEDED`；原任务进入该状态后历史保持可浏览但不可恢复执行。
- 预算不得凭 Rewind 重置：新任务的累计已用量至少等于 checkpoint 时已用量，并记录 lineage 累计量，防止反复回退规避预算。

#### 代码与会话/任务状态

- 先恢复代码并校验 digest，再创建会话/任务分支和转交 lease。
- 若会话分叉失败，使用 pre-rewind checkpoint 回滚代码。
- 成功后新任务从 checkpoint 状态继续，原任务保持不可再运行。

会话分叉是非破坏性的：不删除 checkpoint 后的旧消息、事件和审计记录，也不让旧历史进入新任务上下文。

### 生命周期与清理

- 活跃、paused、pending rewind、recovery-required 或未集成 lineage 的 worktree 不自动删除。
- 删除必须是显式用户动作，并拒绝仍有 lease、未恢复操作或未确认改动的 lineage。
- Snapshot GC 仅删除无数据库引用、超过保留期且不属于 pending operation 的 blob。
- Git worktree prune 仅针对数据库证明已释放且目录缺失/已确认清理的记录。

## 组件边界

### Workspace Feature

- 枚举可快照 inventory、扩展 Snapshot entry 元数据、持久 blob store、restore plan 与 digest 校验。
- `ManagedWorktree` / `WorktreeManager` 只封装固定、安全的 Git worktree 操作，不接受模型提供的任意 Git 参数。
- 不创建 checkpoint 业务记录或决定用户确认。

### Sessions Feature

- 持久化 lineage、snapshot manifest、checkpoint 游标、rewind operation 和任务分支事实。
- 提供原子发布 manifest + checkpoint、非破坏性会话分叉和 pending operation 查询。
- 不直接读写工作区文件或执行 Git。

### Core / Interfaces Feature

- Core 在 Rewind 后失效旧完成候选和 Verification Evidence。
- Interfaces 展示 checkpoint、预览、模式选择、确认、进度和 recovery-required 状态，只委托 Controller。

### 根级集成

- `code_agent_win` 创建 lineage/worktree，再以 worktree root 组合所有 workspace-scoped 服务。
- `CheckpointService` 协调 Sessions 与 Workspace capture。
- `RewindCoordinator` 协调停止 Runtime、恢复 Workspace、会话分叉和 lease 转交。
- 根级不重复 Feature 内部的路径、Git、快照或数据库规则。

## 命令与交互

统一命令注册表增加稳定入口，中文与英文别名遵循现有命令体系：

```text
/checkpoint list
/checkpoint create [label]
/rewind [checkpoint-id]
```

`/rewind` 打开 checkpoint Picker，随后选择三种模式并展示预览。叶子动作只有在显式确认后执行。CLI 提供等价的 list/create/rewind 能力；JSON 模式输出结构化 preview、operation 与结果事件，不进行不可见交互。

## 测试设计

### Workspace

- tracked、deleted tracked、合规 untracked、ignored、敏感、链接/reparse 与越界路径枚举。
- blob 原子发布、hash 校验、去重、损坏与 orphan GC。
- restore 可逆更新、删除和创建，并清除 checkpoint 后新增的合规文件。
- 预检失败不修改任何文件；中途失败可由 rollback snapshot 恢复。
- fingerprint 变化拒绝 stale preview。
- worktree 创建、播种、lease、分支冲突、路径长度和清理保护。

### Sessions

- schema 迁移、required-column 与外键检查。
- snapshot manifest + checkpoint 原子可见。
- checkpoint 游标与三种 rewind operation 往返。
- 非破坏性会话分叉不复制 checkpoint 后记录。
- 原任务不可继续，新任务继承预算且不能重置累计用量。
- pending operation 和 recovery-required 在重启后可查询。

### Coordinator

- 命令树未终止时拒绝 capture/restore。
- code-only、session-only、combined 的正常路径。
- 确认前无副作用，确认后 fingerprint 复验。
- 每个故障注入点都回滚到 pre-rewind 状态。
- 模拟进程在 intent、文件恢复、会话分叉和完成标记之间崩溃，启动对账结果确定。
- rollback 失败时阻断后续写入和命令。
- Rewind 后旧 Verification Evidence 失效。

### 集成与人工验收

- 从带 staged、unstaged 和 untracked 改动的主工作区启动任务，确认主目录字节和 Git index 始终不变。
- 在任务 worktree 中编辑、运行命令、创建多个 checkpoint，并执行三种 Rewind。
- 关闭 TUI、重启、恢复 paused task 和 pending rewind，不重放命令。
- Windows 长路径、锁定文件、磁盘不足、杀进程和 blob 损坏故障演练。
- `/diff`、Context Builder、Runtime、Verification 和子 Agent 全部只观察任务 worktree。

## 实施阶段

1. 需求：更新 Workspace、Sessions、Core、Interfaces Feature 的目标与边界；不填写新增 Units。
2. 实现：依次完成 Workspace inventory/blob/worktree、Sessions schema/repository、Core invalidation、Interfaces Controller/Picker；每个 Feature 内更新 Units 并定向验证。
3. 集成：只在根级 `code_agent_win` 组合 worktree、checkpoint 和 rewind 服务，不再修改 Feature 源码。
4. 验证：Feature 测试、根级集成测试、完整回归和真实 Windows/Git 人工故障演练。

## 完成定义

- 新任务的所有工作区读写、命令与验证都发生在独立 worktree，主工作区保持字节与 index 不变。
- 可恢复 checkpoint 在重启后仍能精确恢复当时的合规代码状态。
- 三种 Rewind 均可预览、确认、审计、失败回滚，并保留原历史。
- crash recovery 不重放命令，不允许 pending/recovery-required lineage 继续写入。
- ignored、敏感、Git 元数据和越界路径永不进入 snapshot 或 restore。
