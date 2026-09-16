# 工作区策略重构（2026-09-15）

把「是否创建 Git worktree」从「当前目录是不是 Git 仓库」改用「这次任务是否需要隔离」来决定。

核心原则：

> Local workspace should be the default execution environment. Git worktree should be an isolation mechanism for parallel or explicitly isolated tasks, rather than a prerequisite for starting every Agent task.

## 改动前的选择流程

```
User submits task
      ↓
foreground_tasks._create_managed_task
      ↓
prepare_workspace(runtime, root)
      ↓
mode = CHAOS_WORKSPACE_MODE（默认 auto）
      ↓
mode == "direct" or 不是 Git 仓库 ?
      ↓ 否
runtime.prepare_task(root, "pending")
      ↓
worktrees.create_unclaimed(...)          # 建 worktree + 分支
      ↓
GitWorkspace(source).changed_snapshot_paths()   # 枚举 tracked changes
      ↓                                        # 枚举全部未忽略 untracked
editor.snapshot(paths)                          # 读取这些文件的字节
      ↓
target.restore(snapshot)                        # 复制进新 worktree
      ↓
Agent 才开始执行用户任务
```

因为默认 `auto` 在 Git 仓库里等价于 managed，所以「解释一下这个函数」这类只读提问也要先付完整的 worktree 初始化成本。

## 改动后的选择流程

```
User submits task
      ↓
foreground_tasks._create_managed_task
      ↓
task_workspace_isolation(root, is_root_busy)
      ↓
      ├── 本根没有其他写入者 → NO_ISOLATION
      └── 本根已有写入者     → isolation_available(root)?
                                 ├── 是 → CONCURRENT_TASK_ISOLATION(parallel)
                                 └── 否 → RuntimeError("already active")
      ↓
prepare_workspace(runtime, root, isolation)
      ↓
plan_workspace(probe, mode=workspace_mode(), isolation)   # 只有需要隔离时才探测 Git
      ↓
      ├── not isolated → source workspace → Agent read/write
      │                   （不探测 Git、不枚举、不 snapshot、不复制）
      └── isolated     → managed worktree
                          ↓
                    changed_snapshot_paths → snapshot → restore
                          ↓
                    Agent read/write
```

## 三种模式的语义

| 模式 | 语义 |
| --- | --- |
| `auto`（默认） | 普通任务用本地 workspace；任务并行（观察到本根已有写入者）或声明了后台 / 显式隔离原因时才用独立 worktree。 |
| `direct` | 始终直接在用户当前 workspace 中读写，绝不创建 managed worktree；本根已有写入者时拒绝启动第二个任务。 |
| `managed` | 始终创建独立 managed worktree，保留原有 dirty-state snapshot / restore 行为。 |

取值非法时在任务启动前拒绝。`managed` 遇到非 Git 根时明确报告「隔离需要仓库」并留在本地；`managed` 初始化失败必须报错，不得静默退回 source root。

## 自适应规则：谁在什么时候被隔离

`auto` 现在是真正的 adaptive mode，输入是**在选择工作区之前就能确定的事实**：

| 事实 | 来源 | `auto` 结果 |
| --- | --- | --- |
| 本根已有活动任务（同一 source root） | `_has_active_source_task()` | 隔离（`auto-isolation:parallel`） |
| 本根无其他写入者 | 同上 | 本地（`auto-local-workspace`） |
| 根不是 Git 仓库 | 惰性探测 | 本地，并在需要隔离时拒绝启动 |
| 本次调用带 `--isolated` | `isolated_tasks("explicit")` 作用域 | 隔离（`auto-isolation:explicit`） |
| 调用方声明是后台 / 委派执行 | `isolated_tasks("background")` 作用域 | 隔离（`auto-isolation:background`） |
| `CHAOS_WORKSPACE_MODE=managed` | 环境变量 | 隔离 |

`adaptive_isolation()` 把事实映射成隔离请求，是纯函数并有单元测试覆盖。

### 隔离作用域（`--isolated` / 后台委派）

运行时不总能在选工作区的那一刻**证明**任务是隔离的。知道这件事的调用方，在自己启动的工作期间声明它：

```python
from code_agent_win.workspace_policy import isolated_tasks

with isolated_tasks("explicit"):     # chaos-agent --isolated
    await foreground_tasks.start(prompt)

with isolated_tasks("background"):   # 后台 / 委派执行器
    await run_delegated(prompt)
```

作用域走 `ContextVar` 传到工作区选择，不需要沿构造链一路透传参数；退出即恢复上一层。CLI 把 `--isolated` 映射为 `explicit` 作用域，ACP 入口（`acp_cli`）复用同一个 `run()`，因此编辑器客户端同样可用。

**声明了却不给，是报错不是降级。** `require_isolation()` 在模式无法隔离（`direct`）或根本不能承载 worktree（非 Git 根）时抛错，并在建 thread / task **之前**发生，消息里带上原因（`direct-mode` / `auto-isolation-requires-git-repository`）。

## 行为变更（需要知道）

以前同一 source root 上的第二个前台任务会被直接拒绝（`a foreground task is already active`）。现在：

- `auto` + Git 仓库 → 第二个任务落在 managed worktree 里继续执行，source root 保持只有一个本地写入者；
- `direct`，或 `auto` + 非 Git 根（无法承载 worktree）→ 仍然拒绝并给出同一错误信息。

隔离后的任务根会出现在任务的 `contract.authorization.workspace_root` 中，可据此在 UI / 日志中看到「这个任务跑在托管 worktree」。

## managed worktree 的回收

managed worktree 是一份完整 checkout。此前没有任何代码在任务结束后删除它，于是 `LOCALAPPDATA/chaos-agent-workspaces/worktrees/<repo>/<lineage>` 每跑一个隔离任务就多一份。`prune` 能力一直存在，但从未被自动调用。

现在 `reclaim_worktrees()` 在每次 `startup()` 跑一次。回收的判断刻意保守，因为 worktree 在未被证明为空之前都属于用户的成果：

| 条件 | 说明 |
| --- | --- |
| 该 lineage 不在本进程的 `_prepared` 里 | 正在用的不动 |
| 该 lineage 没有 pending rewind | 有待恢复的回滚不动 |
| 没有 lineage 记录，或所属任务已到终态 | `interrupted` / `paused` 是可恢复状态，不算终态 |
| 该 lineage 没有 checkpoint snapshot（默认通过） | 有快照就说明还能 rewind，动不得 |
| 是 Git 注册的 worktree，且分支是本工具创建的 `codex/task-<lineage>` | 不是我们建的不碰 |
| 分支 tip 仍指向 worktree 的 HEAD，且 `is_ancestor(HEAD, source HEAD)` | 分支上没有任务自己的提交 |
| checkout 干净 | `status_porcelain` 为空；有未提交改动就不动 |

前一条与最后两条合起来才能推出「目录内容可从仓库复现」：没有快照可回滚、没有提交、没有未提交改动。任何一条不满足都记入 `retained` 并附原因。

**为什么把 snapshot 作为默认门槛**：删除一个还有快照的 worktree，会让一次可执行 rewind 失去还原目标。显式入口 `chaos-agent --reclaim-workspaces`（对应 `reclaim_workspaces(rewindable=True)`）跳过这条门槛并打印 kept / reclaimed 清单，把权衡交回用户。

回收逐项 best-effort：单个 worktree 分类失败或删除失败只记入 `retained`，绝不让启动失败。进程内与创建互斥——每个仓库走 `RepositoryLifecycleLock`。

**回收到一个已存在的缺口**：lineage 是持久记录，worktree 目录是资源，两者寿命不同。回收（或用户手工删除目录）之后，lineage 仍然指向一个不存在的路径，而每次 `startup()` 都会重新 hydrate 它，把该路径登记成 task/thread root。`recover_edit_batches` 随后会对每个 root 构造 `WorkspacePathGuard`，于是「目录不存在」直接让启动抛 `ValueError` —— 一个被删掉的 worktree 会让**整个 CLI 起不来**。

修法是在恢复循环里跳过不存在的 root：没有目录就不可能有未闭合的编辑批次。用户请求的 source root 不会因此被放过——`create_application` 在构造 `WorkspacePathGuard` 时就校验过它了。回归用例：`tests/test_workspace_reclamation.py::FinishedTaskWorktreeTests::test_startup_survives_a_retired_task_worktree`。

**本次验证的副作用（需知悉）**：2026-09-15 在本机以 `--reclaim-workspaces` 实跑过一次，退役了 9 份已完成/失败任务的 worktree（9 条 lineage 现在指向已删除目录）。这些任务不再可 rewind。

## 为什么普通任务不再触发 dirty workspace snapshot

`plan_workspace` 通过 `repository_probe_required(mode, isolation)` 决定是否需要探测 Git：

- `direct` → 不需要；
- `auto` 且未声明隔离原因 → 不需要；
- `managed`，或 `auto` 且已声明隔离原因 → 需要。

命中前两者时直接返回本地计划，`prepare_workspace` 立即返回 `None`，因此 `changed_snapshot_paths`、`snapshot`、`restore` 全部不会被调用。含有 datasets / artifacts / logs / results / cache / 数万个 untracked files 的项目，启动成本与空目录相同。

本次修改刻意不通过放大 `DEFAULT_MAX_OUTPUT_BYTES`（1 MB → 8 MB / 32 MB）来缓解问题：路径列表过大只是表象，真正的问题是普通任务根本不该枚举整个 dirty workspace。managed 路径保留原有的有界保护——超出 Git output 上限仍然是明确的错误，而不是静默的部分复制。

## 本地模式的可执行 checkpoint（rewind 与 worktree 解绑）

可执行 checkpoint、`/rewind` 与 session rewind 都以 workspace lineage 为键。本地任务现在也拥有 lineage，但它的 `worktree_root` 就是 `source_root`：

```
build_local_lineage(root, task_id)
      ↓
repository_id = sha256(normcase(git-common-dir))     # 与 worktree 同一套 id
source_root   = worktree_root = root
branch_name   = git branch --show-current（detached 时为 "detached-head"）
head_commit   = git rev-parse --verify HEAD
      ↓
不建分支、不建目录、不复制任何 dirty 文件
```

由此带来的两点：

1. **`/checkpoint` 与 `/rewind` 在本地模式下可用**：显式创建的 checkpoint 会写入真实的 workspace snapshot，代码 rewind 直接还原 `source_root`。session rewind 的 fork / owner 转移逻辑只依赖 lineage，不需要 worktree。
2. **自动生命周期边界仍然只写 metadata**：`checkpoint_available()` 只在 `worktree_root != source_root`（即隔离任务）时返回 true，所以本地任务在 `task-created` / `task-paused` / `task-completed` 等边界不捕获整棵工作区。否则每次任务启动都会退回「枚举 + 复制整个 dirty workspace」的成本——正是这次改造要消除的东西。本地任务的持久化快照**只在用户显式要求时**产生。

代价：本地任务的 lineage 建立需要 4 次常数大小的 git 调用（`is-repository`、`common-dir`、`HEAD`、`current branch`），与仓库脏文件数量无关。无法建立 lineage 的根（非仓库、空仓库、Git 拒绝）退化为 metadata-only checkpoint，不影响任务启动。

另外，`_CAPTURE_LIMITS` 补上了 `GitOutputLimitError`：隔离任务在超大仓库里做边界快照时，超出 Git output 上限会降级为 metadata-only checkpoint，而不是让任务启动失败。

## 仍然会创建 worktree 的场景

- `CHAOS_WORKSPACE_MODE=managed`（强隔离、并行任务、后台任务、隔离机制测试）；
- `auto` + 观察到本根已有写入者（并行规则）；
- `auto` + 本次调用带 `--isolated`（`explicit` 作用域）；
- `auto` + 调用方在 `isolated_tasks("background")` 里启动任务（后台 / 委派）；
- 未来 remote / cloud execution 需要独立工作目录时，可复用同一套 managed workspace。

## 源码落点

| 文件 | 作用 |
| --- | --- |
| `code_agent_win/workspace_policy.py` | 纯策略：模式解析、`adaptive_isolation()`、隔离作用域（`isolated_tasks` / `request_task_isolation` / `task_isolation_request`）、计划解析、是否需要探测 Git |
| `code_agent_win/foreground_workspace_setup.py` | 把事实映射成隔离请求（`task_workspace_isolation()` / `isolation_available()` / `require_isolation()`），必要时才 `prepare_task`，以及 bind / abort |
| `code_agent_win/local_workspace_lineage.py` | 本地任务的 worktree-free lineage：`build_local_lineage()` / `attach_local_lineage()` |
| `code_agent_win/foreground_tasks.py` | 一次 `task_workspace_isolation(...)` 得到隔离请求，其余调用链不变 |
| `code_agent_win/workspace_seeding.py` | 唯一会枚举并复制 dirty workspace 的地方：`seed_source_changes()` |
| `code_agent_win/workspace_reclamation.py` | 回收判定与执行：`reclaim_worktrees()` / `candidate_worktrees()` / `ReclamationReport` |
| `code_agent_win/workspace_runtime.py` | `checkpoint_available()` 区分隔离 / 本地；`reclaim_workspaces()` 在 `startup()` 跑一次；worktree 创建、dirty seed、lineage、cleanup 全部保留 |
| `code_agent_win/cli_options.py` | CLI 选项解析（`--isolated` / `--reclaim-workspaces` 等），从 `cli.py` 抽出以满足单文件行数规范 |
| `code_agent_win/cli.py` | `--isolated` → `explicit` 作用域；`--reclaim-workspaces` → 显式回收并打印清单 |
| `code_agent_win/workspace_startup_recovery.py` | `recover_workspace_edit_batches()` 跳过已不存在的 task/thread root（回收后的 lineage 指向已删目录，否则启动会被 `WorkspacePathGuard` 打断） |
| `src/code_agent/workspace/_git_worktrees.py` | 新增有界命令 `is_ancestor()`，用于证明任务分支没有自己的提交 |
| `src/code_agent/sessions/_workspace_snapshots.py` | `lineage_has_workspace_snapshots()`：回收的默认快照门槛 |
| `src/code_agent/core/task.py` | `TaskStatus.is_terminal`：终态判定的公共入口 |
| `src/code_agent/checkpoints/service.py` | `_CAPTURE_LIMITS` 增加 `GitOutputLimitError`，边界快照降级而非失败 |

## 测试

| 文件 | 覆盖 |
| --- | --- |
| `tests/test_workspace_policy.py` | 模式解析、隔离请求、自适应映射、隔离作用域（含嵌套与恢复）、CLI 选项解析 |
| `tests/test_workspace_mode_lifecycle.py` | Case 1–6：auto/direct/managed、非 Git 根、海量 untracked（auto 无枚举 / managed 有界报错） |
| `tests/test_workspace_adaptive_isolation.py` | 并发写入者 → managed；本地 checkpoint 语义 |
| `tests/test_workspace_isolation_entry.py` | `explicit` / `background` 作用域 → managed worktree；作用域结束即恢复本地；`direct` 与非 Git 根的拒绝路径 |
| `tests/test_workspace_reclamation.py` | 孤儿 worktree、启动自动回收、终态任务（默认保留 / 显式回收）、有自身提交、有未提交改动、活动任务、空存储、**退役后再次 `startup()` 仍成功** |

## 后续可做

1. **真正的后台 / 委派任务**：`isolated_tasks("background")` 已可用且有测试，但任务生命周期还需要能表达「后台长期运行」这一事实；届时后台执行器只需用该作用域包住自己启动的任务。
2. **回收策略的用户可见性**：可在 TUI 中把 `ReclamationReport.retained` 的原因展示出来，让用户自己决定是否对某个任务的 worktree 执行显式回收。
3. **快照与 lineage 的存量回收**：目前只回收目录，DB 里的 lineage / checkpoint 行保留（体积很小，且是 rewind 与审计的依据）。若将来需要清理，应按 workspace 维度统一 GC。
4. **文件级冲突检测**：目前是「每个根一个本地写入者 + 额外写入者隔离」。若将来允许两个任务共享同一个根，才需要它。
