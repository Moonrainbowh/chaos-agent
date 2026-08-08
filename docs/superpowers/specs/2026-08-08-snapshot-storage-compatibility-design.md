# Snapshot 存储只读兼容与迁移设计

**状态：** 第一阶段已实现；物理迁移与旧 worktree 迁移待独立验收。

## 1. 问题与边界

受管 worktree 已从：

```text
%LOCALAPPDATA%\chaos-agent\managed-workspaces
```

迁到：

```text
%LOCALAPPDATA%\chaos-agent-workspaces
```

旧实现把 durable checkpoint blob 与 worktree 放在同一存储根下，因此路径迁移也改变了 snapshot blob 根。SQLite 只保存相对路径、SHA-256、大小、mode 与 inventory digest，不保存物理 blob 根；旧 checkpoint 的内容仍在，但新进程默认找不到。

本设计只处理 `ContentAddressedSnapshotStore` 管理的 durable checkpoint。`WorkspaceSnapshotStore` 管理的 mutation rewind artifact 是另一套存储，不参与本次兼容。

## 2. 第一阶段：新根只写、旧根只读

Host 固定注入以下顺序：

1. primary：`%LOCALAPPDATA%\chaos-agent-workspaces\snapshots`
2. legacy fallback：`%LOCALAPPDATA%\chaos-agent\managed-workspaces\snapshots`

每个 manifest entry 按 digest 独立查找。读取规则为：

- primary 命中且完整时立即使用，不访问 fallback；
- 只有 root、shard 或 blob 在首次稳定检查时确实不存在，才读取下一根；
- primary 中的对象一旦存在，权限错误、链接或 reparse、类型错误、大小或 hash 不符、identity 变化及读取竞态都立即失败闭合；
- 命中的 blob 每次都重新校验 manifest size 与 SHA-256；
- 整份 manifest 完整物化后才向调用方返回，不暴露部分结果。

fallback 只在 store 构造时准入：字面路径从锚点到 `blobs` 的每个现存组件都必须是真实目录且不是 symlink/reparse。`blobs` 不存在时，本次 store 不启用该 fallback，避免启动后再出现的 junction 被动态信任。

写入边界固定为：

- `put()`、临时文件发布和 GC 只操作 primary；
- fallback 不创建目录、不 copy-on-read、不更新时间戳、不扫描 GC、不删除；
- fallback 路径只由 Host 根据历史产品路径注入，不来自模型、manifest、SQLite 或用户配置。

## 3. 启动恢复

单项 pending rewind 必须只用 operation ID 调用公开恢复入口，并在 lineage lock 内重新读取数据库中的权威 intent：

```text
解析 source task → quiesce → 获取 lineage lock → 校验 owner
→ 物化 rollback snapshot → 恢复 workspace → 校验 inventory digest
→ 标记 rolled_back
```

Runtime 不直接调用 recovery 私有方法，也不在外层重复获取同一 lineage lock。启动第二次执行应为幂等空操作。

第一阶段的端到端保证是：当 lineage 的 worktree 仍可由当前 Guard 安全使用时，旧根中的 checkpoint blob 可用于 preview 和 pending rollback，且读取不会修改旧根。

## 4. 明确不自动处理的旧 worktree

历史 lineage 的 `worktree_root` 可能仍指向 `%LOCALAPPDATA%\chaos-agent\managed-workspaces\worktrees\...`。该目录位于本地 API 配置保护根下，当前 Guard 会拒绝访问。

本阶段不进行以下高风险操作：

- 不全局放宽 `%LOCALAPPDATA%\chaos-agent`；
- 不根据路径形状直接信任旧 worktree；
- 不自动执行 `git worktree move`；
- 不改写 lineage 数据库路径；
- 不删除旧目录。

因此不能把“旧 blob 可读”描述成“所有历史任务均已完整恢复”。旧 worktree 迁移需要独立事务设计，并同时验证 task owner、lineage、repository identity、Git registration、branch、HEAD、路径组件和无链接约束。

## 5. 后续显式 blob 迁移

未来迁移工具应是显式、幂等、可中断且只增不减的离线操作：

1. 从 SQLite 枚举仍被引用的 manifests 与 digests，但不信任其中的物理路径。
2. 使用与运行时相同的安全读取器验证 legacy blob 的类型、identity、size 与 SHA-256。
3. 仅对 primary 缺失的 digest，通过 primary 的原子发布逻辑复制；禁止直接 move 或覆盖。
4. 重新从 primary 物化全部被引用 manifest，并比对 inventory facts。
5. 输出可审计报告：扫描数、已存在数、复制数、缺失数、损坏数和失败 manifest。
6. 保留 legacy 根作为回退；迁移成功不等于获得删除授权。

只有在所有数据库引用均可从 primary 完整物化、旧 lineage 不再引用旧 worktree，并得到用户单独确认后，才能设计可恢复的清理步骤。

## 6. 验收矩阵

- primary 有效时不读取损坏的 legacy 副本；
- primary 缺失、legacy 有效时成功物化，两个根均不被修改；
- primary 存在但损坏时，即使 legacy 有效也失败；
- 一个 manifest 的 blobs 分布在两个根时逐项正确读取；
- 两根均缺失时保持原有失败类型和消息；
- 新 snapshot 只写 primary，GC 不触碰 legacy；
- legacy 根不存在时读取不会创建它；
- 真实 SQLite pending intent 在重启后回滚、变为 `rolled_back`，旧根内容与 mtime 不变，第二次启动无重复动作。
