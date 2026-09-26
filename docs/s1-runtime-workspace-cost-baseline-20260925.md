# S1：Runtime、工作区与工具成本基线

## 目标

本阶段只建立事实基线和后续任务边界，不改变 Runtime、工作区快照、CAS、Git 或工具调度行为。

本报告服务于后续 S2-S5：

- S2：收敛 Windows/POSIX 宿主 Runtime，并移除 Docker；
- S3：根据实际数据降低 CAS 与 Git 的重复成本；
- S4：根据实际调用数据降低工具往返成本；
- S5：完成集成、文档和最终验收。

## 工作区状态

开始 S1 时工作区已经存在未提交改动：

- `src/code_agent/core/AGENTS.md`
- `src/code_agent/core/_engine_convergence.py`
- `src/code_agent/core/_engine_run.py`
- `src/code_agent/core/_engine_turn.py`
- `src/code_agent/core/tests/test_exploration_repeat.py`
- `src/code_agent/interfaces/terminal_state.py`
- `src/code_agent/interfaces/tests/test_terminal_state.py`
- `scripts/generate_brine_charts.py`

这些改动不属于本 S1 任务，未读取后修改、未回滚、未纳入本报告的结论。

## 一、Runtime 基线

### 当前实现

当前 Runtime 枚举仍包含：

- `local`
- `docker`

本机 Runtime 已经按平台分为：

- Windows：`WindowsLocalRuntime`，PowerShell 方言，Windows Job Object；
- POSIX：`PosixLocalRuntime`，`/bin/sh` 方言，独立 process group。

Docker Runtime 位于 `src/code_agent/runtime/docker.py`，并且其当前实现固定使用 `WindowsLocalRuntime` 启动 Docker CLI。这是 S2 的删除对象，不再修复或扩展 Docker 跨平台能力。

### 目标边界

S2 将收敛为：

```text
Windows
  └─ PowerShell 7 + Windows Job Object

Linux / macOS / WSL2
  └─ POSIX Shell + process group
```

Windows 不再回退到 Bash：找不到 PowerShell 7 时返回明确的 Runtime 不可用结果。非 Windows 不探测 PowerShell。

本机 Runtime 仍然只是受控宿主进程执行器，不作为 OS 级沙箱宣传。

## 二、CAS 与 Git 基线

### 当前职责分布

Git 负责：

- 仓库检测；
- tracked/non-ignored-untracked 路径枚举；
- dirty 状态和 diff；
- managed worktree 创建、删除和生命周期校验；
- 隔离任务的 Git 分支和 worktree 身份。

CAS/快照负责：

- checkpoint/rewind；
- dirty baseline 的内容保存；
- 多文件编辑恢复；
- 普通非 Git 工作区的恢复存储；
- blob 去重、完整性校验和 orphan GC。

当前不能直接得出“CAS 完全重复 Git”的结论，因为两者保存的是不同层次的事实：Git 保存仓库状态和 worktree 拓扑，CAS 保存 Agent 恢复所需的内容镜像。

### 已确认的默认成本边界

现有工作区策略已经将普通任务和隔离任务区分开：

- `auto/direct` 使用 source workspace；
- 普通任务不因启动而枚举 dirty workspace；
- `managed`/isolated 路径才调用 `changed_snapshot_paths`、snapshot 和 restore 以种植 dirty 状态；
- checkpoint/rewind 依赖 workspace lineage 和可用的 checkpoint 能力；managed 任务使用独立 worktree，direct/local 任务可使用 `worktree_root == source_root` 的 worktree-free lineage，无法建立完整 lineage 时降级为 metadata-only checkpoint；
- snapshot store 自身有文件数、单文件大小、总字节数、deadline 和 GC 限制。

因此，S3 不会直接删除 CAS，而会先围绕真实 snapshot 路径测量：

- Git changed path 数量；
- snapshot entry 数量；
- 唯一 blob 数量；
- 输入总字节数；
- blob store 增量字节数；
- snapshot、materialize、restore、GC 各阶段耗时；
- ignored/generated/virtualenv 目录是否进入实际路径集合。

### S3 的暂定方向

在没有 benchmark 之前，不改变行为。测量完成后优先考虑：

1. 普通 local/direct 任务继续不做全量 snapshot；
2. managed 任务只保存 dirty baseline 和必要恢复路径；
3. Git worktree 继续负责隔离，不让 CAS 模拟 Git branch/worktree；
4. CAS 只保存 rewind/edit recovery 所需 bytes 和 metadata；
5. 大型生成目录和依赖目录不能因为 Agent checkpoint 被无界纳入。

## 三、工具往返成本基线

### 当前已有数据

仓库已经具备以下局部观测：

- `with_action_duration` 为单个 action 记录 `duration_ms`；
- 主任务持久化 `model_turns`、`tool_calls` 和 usage；
- 子 Agent 的 `AgentUsage` 记录 token 数、tool call 数和运行秒数；
- task budget 持久化模型回合和工具调用上限；
- continuity/evaluation 路径已有 actions、usage 和预算报告。

### 当前缺口

现有数据还不能回答：

- 一个用户任务的端到端工具等待时间；
- action p50/p95/p99；
- 重复读取同一文件的次数和耗时；
- Git inventory、Workspace inventory、编辑、验证各自占用的时间；
- 失败重试带来的额外调用；
- 单文件简单修改与多文件修改的真实调用差异；
- provider 等待时间与本地工具执行时间的比例。

因此，S4 不能仅凭 `tool_calls` 数量做优化结论。需要补充任务级聚合，但不能在 S1 为此改变生产流程。

### S4 的测量任务

至少覆盖：

1. 读取一个文件并回答；
2. 修改一个端口配置；
3. 修改两个相关文件；
4. 修改后运行一次固定验证；
5. 失败后恢复；
6. 大量 untracked 文件仓库中的单文件修改；
7. managed worktree 中带 dirty baseline 的修改。

每个场景记录：

- model turns；
- tool calls；
- 本地 action 总耗时和分位数；
- 文件读取次数；
- 重复读取次数；
- Git 调用次数和耗时；
- snapshot/restore 耗时；
- verification 次数和耗时；
- retry/failure 数量；
- provider usage。

优化只允许合并重复工作或增加批量能力，不得为了降低调用次数跳过授权、路径校验、precondition hash、原子写入或结果验证。

## 四、S1 验证结果

### 通过的基线测试

执行：

```text
python -m unittest \
  src.code_agent.runtime.tests.test_models \
  src.code_agent.runtime.tests.test_posix \
  src.code_agent.workspace.tests.test_git \
  src.code_agent.workspace.tests.test_snapshot_store
```

结果：

- 44 个测试执行，其中 39 个通过；
- 5 个 POSIX 执行测试在 Windows 环境按条件跳过；
- Runtime models、Git workspace、CAS snapshot store 的边界测试通过。

另执行：

```text
python -m unittest discover -s tests -p test_workspace_mode_lifecycle.py -v
```

结果：

- 7 个测试通过；
- `auto/direct` 不枚举 source workspace 的测试通过；
- 大量 untracked 文件不拖慢 auto task 的测试通过；
- managed 模式保留有界 Git dirty-state protection 的测试通过。

第一次尝试将仓库根目录测试误作为 `src.code_agent.workspace.tests.test_workspace_mode_lifecycle` 导入，导致模块找不到；随后使用正确的 `tests` discovery 命令重新执行并通过。这是测试选择错误，不是产品测试失败。

### 尚未测量的项目

以下内容不能在 S1 中假设已经完成：

- 真实大型仓库 CAS/snapshot 的文件数、字节数和耗时；
- 完整 task-level tool latency p50/p95；
- 重复读取比例；
- provider latency 与本地工具 latency 的比例；
- Linux/macOS/WSL2 上的真实 POSIX process group 运行结果。

## 五、S2-S5 文件范围

### S2

重点范围：

- `src/code_agent/runtime/`；
- `src/code_agent/runtime/tests/`；
- Runtime capability 和平台选择相关 Feature 文件。

主要动作：删除 Docker，固定 Windows PowerShell 7-only 和非 Windows POSIX-only。

### S3

重点范围：

- `src/code_agent/workspace/snapshot_store.py`；
- `src/code_agent/workspace/_snapshot_*`；
- `src/code_agent/workspace/git.py`；
- `code_agent_win/local_workspace_lineage.py`；
- `code_agent_win/workspace_seeding.py`；
- `code_agent_win/workspace_checkpoint_runtime.py`；
- `src/code_agent/checkpoints/`；
- `src/code_agent/workspace/inventory.py`、`edits.py`、`rewind_state.py`；
- 相关 sessions workspace/checkpoint 模型、repository 和测试；
- 对应 workspace/checkpoint 测试。

主要动作：以 benchmark 为依据降低不必要的 snapshot 和 CAS/Git 重复成本。上述文件是职责边界和验证范围，不代表 S1 已经修改这些实现；S3 只修改 benchmark 证明需要修改的部分。

### S4

重点范围：

- `code_agent_win/action_support.py`；
- `code_agent_win/action_dispatcher.py`；
- `code_agent_win/task_verification.py`；
- task/session/evaluation metrics 相关模块；
- benchmark 和报告脚本。

主要动作：补任务级成本统计，再实现批量读取、批量验证或受控 fast path。

### S5

重点范围：

- 根目录入口和配置；
- `README.md`；
- Runtime、workspace 和安全边界文档；
- 全量集成测试和平台验证。

主要动作：同步用户可见行为、文档、提示和最终验收结果。

## S1 结论

- CAS 不能在没有数据的情况下直接删除；应保留其 rewind/edit recovery 职责，并用 S3 benchmark 判断减负范围。
- Git worktree 已经承担隔离职责，S3 不应让 CAS 重复模拟 Git worktree 或 Git 历史。
- 普通 `auto/direct` 工作区已经有避免全量 dirty 扫描的保护，当前主要风险集中在 managed/checkpoint/rewind 路径。
- 工具成本已有 action duration、tool calls 和 usage 基础，但缺少任务级聚合和重复工作指标；S4 需要补齐数据后再优化。
- Docker 删除属于 S2，不在 S1 修改。
- S1 基线报告完成，下一步应先进行独立监督，再决定是否进入 S2。
