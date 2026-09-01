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
- 安全边界：POSIX inventory/restore/blob store 以 `dir_fd`、`O_NOFOLLOW` 和逐级目录句柄约束读写、建目录、替换、扫描与删除；受管 worktree lifecycle 以 storage 内 repository lock 协调遵守协议的跨进程 create/remove/prune；Windows 通用路径依赖静态 reparse 拒绝及副作用前后身份复验，精确批次 move/delete 额外持有逐级目录和源文件原生句柄并使用 no-replace rename/delete；这些约束仍不构成操作系统沙箱。
- Windows 长路径边界：每次 Git for Windows 调用以进程级 `core.longPaths=true` 运行且不修改用户/仓库配置；CPython 文件路径依赖系统 `LongPathsEnabled=1`，关闭时统一采用 240 UTF-16 code unit legacy-safe 预算并在文件系统副作用前显式失败，inventory 不得静默漏掉超限路径；Git worktree 另受不可由用户放宽的 215 单元/UTF-8 byte 预算约束；snapshot root 按最长派生 blob/temp 路径在构造期预检；`/状态` 与 Provider prompt 显示当前快照。
- 依赖：Chaos Agent 和旧 code-agent 的本地配置目录均视为敏感路径；`chaos-agent-workspaces` 是产品保留目录名，从其他 workspace 或其父目录不可访问，存储根、`worktrees`/仓库中间层与 snapshot 子树也不得直接作为 workspace，只有 `worktrees/<repo>/<lineage>` 任务根及其子目录可正常操作相对内容。
- 负责为成功 typed 写入记录 `ActionEffect` 所需的改动路径与内容 hash，并在有界扫描内计算当前 subject snapshot/hash；不判断业务正确性。

- 负责：为已规划的 typed edit 生成精确 bytes/existence 前镜像、确定性前后 hash、当前路径状态与相关路径摘要。
- 不负责：Action 归因、checkpoint 排序、Sessions journal 或 rewind 可用性裁决。

## Units
- `SubjectSnapshot`、`snapshot_subject(...)`: 为改动文件和关键 manifest 生成有界、确定性的 subject hash | 读取受 guard 保护的工作区文件 | 不判断业务正确性或扫描工作区外路径
- `WorkspaceError`、`WorkspacePathGuard(root)`：表达失败并执行路径规范化、containment 与敏感策略 | 保留 Host 提供的字面根以兼容系统根别名，但解析和逐组件元数据检查统一锚定 canonical root | `allow_outside` 仅供已批准 dispatcher；链接/reparse 与大小写变体的受保护元数据始终拒绝
- `IgnoreRules.from_workspace(root): IgnoreRules`：严格以 UTF-8/UTF-8 BOM 加载内置忽略项和根 `.gitignore` 的常用规则子集 | 读取根 `.gitignore` | 只有文件缺失才返回纯内置规则；读取或解码失败显式中止，不能静默扩大可见范围；支持顺序反选，不是完整 Git parser
- `windows_path_support()`、`windows_path_units(...)`、`require_supported_windows_path(...)`: 缓存当前进程看到的 `LongPathsEnabled`、按 UTF-16 code unit 计量并给出 240/32000 安全预算 | 只读 HKLM 策略 | 不修改注册表；关闭策略时错误必须给出启用 Win32 long paths 和重启提示
- `TextFileFormat`、`decode_text_bytes(...)`、`encode_existing_text(...)`：严格识别 BOM UTF-8/16/32 或无 BOM UTF-8，并按显式 Windows ANSI/OEM code page 无损往返 | 不猜测其他编码、不使用 replacement character；一致 CRLF/LF/CR 可继承，mixed 保持原样
- `WorkspaceFiles.list_files/list_known_files/read_text/search(...)`、`invalidate_inventory()`：有界枚举、过滤已知候选、读取和搜索可访问文件，`read_text` 同时返回编码、BOM、code page 与换行元数据，并将省略 root、`.` 和工作区绝对根统一复用短期根 inventory | 读取工作区文件 | 候选仍逐项经过 guard、存在性和 ignore 校验；扫描、大小、结果与全局 deadline 超限显式失败，不返回伪完整结果
- `WorkspaceEditor`: 有界读取现有文件，生成携带精确输出 bytes/格式的写入或单次替换 Diff，保留既有编码、BOM 与一致换行风格，并校验哈希后原子应用 | 单文件同目录临时写入与替换；新文件默认 UTF-8 无 BOM
- `BatchEditPlan`、`DeletePlan`、`MovePlan`、`WorkspaceEditor.plan_batch/apply_batch(...)`: 生成不可变 create/update/delete/move 批次并记录所有源/目标的精确存在、SHA-256 与大小 | 全量 preflight、每步 CAS、Windows no-replace 同卷 move 与逆序 owned-only 回滚 | 拒绝重叠路径、rename 链/swap、异卷 move 和静默覆盖；仅大小写 move 是唯一同 canonical path 例外
- `PreparedBatchEdit`、`RecoveryOperation`、`PathTransition`、`snapshot_from_prepared/recover_batch(...)`: 将 durable journal 所需的纯 PRE/POST 转换和恢复入口留在 Workspace | 初始任一 FOREIGN 零写入，之后只逆序恢复 POST 并逐步复核 | case-only move snapshot 只保存 source preimage；中途 foreign 内容保留并返回 partial conflict
- `WorkspaceSnapshot`、`build_restore_snapshot(...)`: 复制字节、补充 tombstone 并按依赖恢复文件/目录拓扑 | 首次写前以共享 entry/deadline 预算迭代扫描路径、blob、容量、权限和冲突内容，深度优先删除并安全重建父目录，替换后复验稳定身份、大小与内容 hash；失败清理仅删除可由 fd/entry 身份共同证明的 owned temp，且不遮蔽 primary error | 不删除 ignored、敏感或未纳入 tombstone 的目录内容；不承诺多文件事务原子性
- `BlobRef`、`SnapshotManifestEntry`、`SnapshotManifest`、`MaterializedSnapshot`: 表达排序且可确定摘要的持久快照清单、tombstone、blob 引用与 mode 映射 | 无副作用 | store 只落 blob，不写会被用户工作区 snapshot 捕获的旁路 manifest
- `ContentAddressedSnapshotStore.put/materialize/delete_orphans(...)`: 原子发布并去重内容寻址 blob，完整校验后物化快照，并有界回收旧 orphan；可接受 Host 固定注入且构造时逐组件验证的只读 fallback roots | temp 创建成功即在函数内持有并以稳定 identity 清理，cleanup failure 仅附加而不遮蔽 primary；POSIX 以 verified directory fd 执行同 shard 临时写、flush/fsync、digest 复验、replace、scan 与 unlink；Windows 在路径副作用前后复验 root/shard 身份；GC 的 referenced 与 store scan 共享 deadline/entry 预算 | put/temp/GC 只操作 primary；仅 root/shard/blob 初始确实缺失时逐 digest 回退，损坏、权限、链接/reparse、identity 变化、misplaced digest、异常拓扑及超限均立即失败闭合；fallback 不存在时不动态启用，也不创建、回填、扫描或删除
- `WorkspaceSnapshotStore.save/load(...)`、`SnapshotHandle`：为 Rewind 保存绑定工作区身份的受保护前镜像并按句柄完整校验恢复 | 仅写入 Host 注入的产品状态目录 | 与 checkpoint 内容寻址 blob store 并存，不接受跨工作区句柄或任意 artifact 路径；Windows 拒绝尾点、尾空格、设备名及解析为不同 canonical spelling 的 alias（含现存 8.3），但允许只改变大小写后的历史路径恢复
- `GitWorkspace`、`RepositoryIdentity`、`ManagedWorktree`、`WorktreeManager`: 提供有界仓库检测、状态、全量或变更 snapshot path、指定路径 tracked 判定、Diff，以及受管任务 worktree 的稳定 common-dir 身份、创建、删除和可信缺失记录清理 | Git 子进程清除外部 repository binding、禁用 prompt 并仅执行固定参数，沿用流式输出上限/超时；成功文本严格 UTF-8，失败诊断不能解码时使用显式 Base64 并保留原始 bytes；changed paths 支持尚无 HEAD 的新仓库；create 成功后复验 source 与 target identity/HEAD/branch/registration，失败仅在字面 target 未链接且 exact registration/branch/common-dir identity 均可证明时补偿 | source 必须是 Git top-level，分支严格绑定 `codex/task-<lineage-id>`；非 UTF-8 patch/control output 显式失败而不注入 replacement character；同仓库 create/remove/prune 持有有界跨进程 lifecycle lock；删除要求字面确认、boolean inactive 且干净，prune 在锁内最终 lstat 后仅对可信 missing path 做精确 remove，不调用 repository-wide prune
- `WorkspaceInventory.capture(...)`、`workspace_fingerprint(...)`：枚举并摘要合规 tracked/untracked 代码状态 | Git 使用剩余 deadline，文件分块读取并复验最终身份 | 排除 ignored、敏感、链接/reparse、重复与越界路径

## 环境依赖
- 运行：Python 3.10+ 与 PyPI `regex`（为用户正则提供单次匹配 timeout）。
- 测试：`uv run --with regex python -m unittest discover -s src/code_agent/workspace/tests -v`
