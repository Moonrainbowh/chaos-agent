# S3：CAS 与 Git 成本测量结论

## 目标

用可重复的 synthetic Git workspace 测量 Git inventory、变更路径筛选、文件读取、CAS 写入和物化的实际成本，再决定是否修改生产路径。S3 不凭“CAS 与 Git 重叠”的印象删除 CAS，也不把一次机器上的绝对毫秒数当成普遍性能承诺。

## 测试方法

运行：

```text
python scripts/benchmark_cas_git.py
```

默认 fixture：

- 1000 个 payload 已提交文件，另有 1 个已提交 `.gitignore`；
- 200 个已修改文件；
- 50 个未跟踪文件；
- 500 个 `.gitignore` 忽略文件；
- 1 个 tracked 文件删除，用于覆盖 tombstone；
- 变更文件内容包含重复模式，用于观察 CAS 去重；
- 分别测量 `GitWorkspace.snapshot_paths()` 的完整候选集合和 `changed_snapshot_paths()` 的变更集合；
- 额外测量实际 `WorkspaceInventory.capture()` 的读取、hash 和 mode 成本；
- 在同一个 CAS store 中第二次重复 `put()`，观察跨 checkpoint 的既有 blob 校验成本。

脚本只创建临时仓库，不修改当前工作区和生产代码路径。输出同时包含每次 raw run 和 `median_ms` 聚合字段，可直接审计报告中的中位数。本次原始输出保存在 [`s3-cas-git-benchmark-20260926.json`](s3-cas-git-benchmark-20260926.json)。

## 一次实测结果

Windows 运行结果（2026-09-26，Python 当前环境，3 次独立临时仓库运行；表中为中位数）：

| 路径 | 文件数 | 输入字节 | 读取/捕获耗时 | CAS 唯一 blob | CAS blob 字节 | 首次 put | 重复 put | materialize |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Git 完整候选 | 1051（1050 existing + 1 tombstone） | 165,626 | Git inventory 55 ms；`WorkspaceInventory.capture` 5,808 ms；读取 7,072 ms | 283 | 46,730 | 2,362 ms | 211 ms | 532 ms |
| Git 变更集合 | 251（250 existing + 1 tombstone） | 41,760 | changed-paths 318 ms；读取 1,453 ms | 250 | 41,760 | 2,110 ms | 168 ms | 119 ms |

该结果只用于判断数量级；磁盘、杀毒软件、Git 版本和机器负载会影响绝对值。

## 结论

1. **“CAS 完全重复 Git”未被数据证实。** Git 返回的是仓库路径、tracked/untracked 状态和变更集合；CAS 保存的是 checkpoint 所需的文件 bytes、tombstone、mode 和完整性摘要。两者的职责和数据形态不同。
2. **实际 checkpoint inventory 的 hash/安全读取成本不可忽略。** 完整候选的 `WorkspaceInventory.capture()` 中位数约 5.8 秒；普通完整 snapshot 读取约 7.1 秒。变更集合只读取 251 个路径，约 1.5 秒。当前 `changed_snapshot_paths()` 已经避免了把 500 个 ignored 文件纳入变更 snapshot；普通 direct/auto 路径也已有不启动时全量枚举的保护。
3. **同一快照的 CAS 重复写入会复用已有 blob。** 同一 store 对同一 snapshot 第二次 `put()` 只需约 0.21 秒，明显低于首次写入约 2.4 秒；完整集合 165,626 字节最终只落 46,730 字节。这个 benchmark 不把该结果外推为所有不同 checkpoint 的跨 checkpoint 性能结论。CAS 的既有 blob 完整性校验仍然保留，不能为了速度跳过。
4. **当前没有足够证据把 CAS 改成 Git blob 引用。** 这样会增加对 Git object database、普通目录和 dirty baseline 的耦合，并可能失去非 Git workspace、tombstone、mode 与独立 rewind 生命周期能力。

## S3 决策

本阶段不删除 CAS、不让 CAS 模拟 Git worktree，也不把 Git commit/object database 作为 checkpoint 存储后端。保留现有边界：

- Git：仓库检测、tracked/non-ignored-untracked inventory、dirty diff、worktree 生命周期；
- CAS：checkpoint/rewind、dirty baseline bytes、tombstone/mode、完整性校验和 orphan GC；
- 普通 direct/local：继续避免启动时全量 dirty snapshot；
- managed/checkpoint/rewind：继续只对 Git 变更路径或明确 checkpoint 路径做 snapshot。

后续只保留两个低风险方向，暂不实施：

1. 在真实项目样本上重复运行 benchmark，采集 p50/p95，而不是继续优化 synthetic fixture 的绝对值；
2. 若真实样本确认大量重复 checkpoint 的 `materialize` 或 manifest 校验占主要时间，再针对该路径做受限优化，并保持完整性验证。

## 验证

- benchmark 脚本运行成功并输出 JSON；
- S1 workspace/CAS 测试继续作为行为门禁；
- 本阶段没有修改生产 CAS、Git 或 checkpoint 行为。
