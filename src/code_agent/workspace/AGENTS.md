# Workspace
提供受工作区边界约束的代码发现、读取、搜索、补丁、Diff 与版本控制操作。

## 边界
- 负责：创建受管任务 worktree，枚举合规 tracked/untracked 文件，并持久化内容寻址快照。
- 负责：路径规范化与 containment、忽略规则、文件读取、目录浏览、文本搜索和原子补丁。
- 负责：生成可审阅 Diff，并在 Git 仓库中提供 checkpoint 与 revert 所需的工作区能力。
- 负责：仅通过 Host 注入且经策略明确授权的仓库外 artifact storage 保存 snapshots，并恢复回合前 bytes，包括 dirty baseline；不接受任意外部路径。
- 负责：以无副作用能力探测区分 Git 仓库与普通目录，供集成层决定是否暴露 Git 工具。
- 不负责：执行任意 shell 命令、调用模型、决定是否批准动作或渲染 Diff。
- 不负责：决定 checkpoint 业务语义、用户确认、会话分叉或 Rewind 状态。
- 不负责：未获上层策略授权的工作区之外路径、符号链接/reparse 目标或敏感文件。
- 不负责：访问默认本地 API 配置目录及其内容，即使该目录被选作工作区。
- 不负责：在普通目录中伪造 Git 状态、自动初始化仓库或决定上层是否注册 Git 工具。
- 安全边界：POSIX inventory/restore/blob store 以 `dir_fd`、`O_NOFOLLOW` 和逐级目录句柄约束读写、建目录、替换、扫描与删除；受管 worktree lifecycle 以 storage 内 repository lock 协调遵守协议的跨进程 create/remove/prune；Windows 依赖静态 reparse 拒绝及路径副作用前后身份复验，未使用原生 NT 句柄 API，因此无法消除不遵守该锁的同用户恶意进程并发切换 junction/target 的残余竞态，也不构成操作系统沙箱。
- 依赖：Chaos Agent 和旧 code-agent 的本地配置目录均视为敏感路径；`chaos-agent-workspaces` 是产品保留目录名，从其他 workspace 或其父目录不可访问，存储根、`worktrees`/仓库中间层与 snapshot 子树也不得直接作为 workspace，只有 `worktrees/<repo>/<lineage>` 任务根及其子目录可正常操作相对内容。
- 负责为成功 typed 写入记录 `ActionEffect` 所需的改动路径与内容 hash，并在有界扫描内计算当前 subject snapshot/hash；不判断业务正确性。

- 负责：为已规划的 typed edit 生成精确 bytes/existence 前镜像、确定性前后 hash、当前路径状态与相关路径摘要。
- 不负责：Action 归因、checkpoint 排序、Sessions journal 或 rewind 可用性裁决。

## Units
- `SubjectSnapshot`、`snapshot_subject(...)`: 为改动文件和关键 manifest 生成有界、确定性的 subject hash | 读取受 guard 保护的工作区文件 | 不判断业务正确性或扫描工作区外路径
- `WorkspaceError`、`WorkspacePathGuard(root)`：表达失败并执行路径规范化、containment 与敏感策略 | 保留 Host 提供的字面根以兼容系统根别名，但解析和逐组件元数据检查统一锚定 canonical root | `allow_outside` 仅供已批准 dispatcher；链接/reparse 与大小写变体的受保护元数据始终拒绝
- `IgnoreRules.from_workspace(root): IgnoreRules`：加载内置忽略项和根 `.gitignore` 的常用规则子集 | 读取根 `.gitignore` | 内置排除 Git/产品状态、`chaos-agent-workspaces` 与缓存目录；支持顺序反选，不是完整 Git parser
- `WorkspaceFiles.list_files/list_known_files/read_text/search(...)`、`invalidate_inventory()`：有界枚举、过滤已知候选、读取和搜索可访问文件，并将省略 root、`.` 和工作区绝对根统一复用短期根 inventory | 读取工作区文件 | 候选仍逐项经过 guard、存在性和 ignore 校验；扫描、大小、结果与全局 deadline 超限显式失败，不返回伪完整结果
- `WorkspaceEditor`: 有界读取现有文件，生成写入/单次替换 Diff 并校验哈希后原子应用 | 单文件同目录临时写入与替换
- `WorkspaceSnapshot`、`build_restore_snapshot(...)`: 复制字节、补充 tombstone 并按依赖恢复文件/目录拓扑 | 首次写前以共享 entry/deadline 预算迭代扫描路径、blob、容量、权限和冲突内容，深度优先删除并安全重建父目录，替换后复验稳定身份、大小与内容 hash；失败清理仅删除可由 fd/entry 身份共同证明的 owned temp，且不遮蔽 primary error | 不删除 ignored、敏感或未纳入 tombstone 的目录内容；不承诺多文件事务原子性
- `BlobRef`、`SnapshotManifestEntry`、`SnapshotManifest`、`MaterializedSnapshot`: 表达排序且可确定摘要的持久快照清单、tombstone、blob 引用与 mode 映射 | 无副作用 | store 只落 blob，不写会被用户工作区 snapshot 捕获的旁路 manifest
- `ContentAddressedSnapshotStore.put/materialize/delete_orphans(...)`: 原子发布并去重内容寻址 blob，完整校验后物化快照，并有界回收旧 orphan；可接受 Host 固定注入且构造时逐组件验证的只读 fallback roots | temp 创建成功即在函数内持有并以稳定 identity 清理，cleanup failure 仅附加而不遮蔽 primary；POSIX 以 verified directory fd 执行同 shard 临时写、flush/fsync、digest 复验、replace、scan 与 unlink；Windows 在路径副作用前后复验 root/shard 身份；GC 的 referenced 与 store scan 共享 deadline/entry 预算 | put/temp/GC 只操作 primary；仅 root/shard/blob 初始确实缺失时逐 digest 回退，损坏、权限、链接/reparse、identity 变化、misplaced digest、异常拓扑及超限均立即失败闭合；fallback 不存在时不动态启用，也不创建、回填、扫描或删除
- `WorkspaceSnapshotStore.save/load(...)`、`SnapshotHandle`：为 Rewind 保存绑定工作区身份的受保护前镜像并按句柄完整校验恢复 | 仅写入 Host 注入的产品状态目录 | 与 checkpoint 内容寻址 blob store 并存，不接受跨工作区句柄或任意 artifact 路径
- `GitWorkspace`、`RepositoryIdentity`、`ManagedWorktree`、`WorktreeManager`: 提供有界仓库检测、状态、全量或变更 snapshot path、Diff，以及受管任务 worktree 的稳定 common-dir 身份、创建、删除和可信缺失记录清理 | Git 子进程清除外部 repository binding、禁用 prompt 并仅执行固定参数，沿用流式输出上限/超时；create 成功后复验 source 与 target identity/HEAD/branch/registration，失败仅在字面 target 未链接且 exact registration/branch/common-dir identity 均可证明时补偿 | source 必须是 Git top-level，分支严格绑定 `codex/task-<lineage-id>`；同仓库 create/remove/prune 持有有界跨进程 lifecycle lock；删除要求字面确认、boolean inactive 且干净，prune 在锁内最终 lstat 后仅对可信 missing path 做精确 remove，不调用 repository-wide prune
- `WorkspaceInventory.capture(...)`、`workspace_fingerprint(...)`：枚举并摘要合规 tracked/untracked 代码状态 | Git 使用剩余 deadline，文件分块读取并复验最终身份 | 排除 ignored、敏感、链接/reparse、重复与越界路径

## 环境依赖
- 运行：Python 3.10+ 与 PyPI `regex`（为用户正则提供单次匹配 timeout）。
- 测试：`uv run --with regex python -m unittest discover -s src/code_agent/workspace/tests -v`
