# Runtime Intelligence 与扩展闭环 Program Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> 实施状态（2026-07-20）：Task 1–8 已完成自动化实现与测试；Task 9 的真实配置人工验收仍待执行。下方复选框保留原始执行计划，不作为当前状态源。

**Goal:** 按同一设计交付 Thread Intelligence、Workflow DAG、Plugin Host 接线以及 Skills/MCP TUI 控制面，同时保持 Feature 边界、TDD 和现有安全完成门。

**Architecture:** 四条子计划共享 Sessions 迁移、两级线程身份和动态 Host snapshot，但分别完成 Feature 代码及定向测试。所有 `src/code_agent/**` 工作完成并验证后，才进入 `code_agent_win/**` 根级集成；任何局部失败均回退到现有确定性上下文或禁用单个贡献。

**Tech Stack:** Python 3.10、asyncio、SQLite、unittest、MCP Python SDK、Windows Terminal。

---

## 子计划

1. `docs/superpowers/plans/2026-07-20-thread-intelligence-authorization.md`
2. `docs/superpowers/plans/2026-07-20-workflow-dag.md`
3. `docs/superpowers/plans/2026-07-20-plugin-host-wiring.md`
4. `docs/superpowers/plans/2026-07-20-skills-mcp-tui.md`

## 全局文件边界

### 阶段 1：需求契约

- 修改：`src/code_agent/context/AGENTS.md`
- 修改：`src/code_agent/core/AGENTS.md`
- 修改：`src/code_agent/interfaces/AGENTS.md`
- 修改：`src/code_agent/mcp/AGENTS.md`
- 修改：`src/code_agent/orchestration/AGENTS.md`
- 修改：`src/code_agent/plugins/AGENTS.md`
- 修改：`src/code_agent/sessions/AGENTS.md`
- 修改：`src/code_agent/skills/AGENTS.md`
- 修改：`src/code_agent/thread_intelligence/AGENTS.md`
- 创建：`src/code_agent/workflows/AGENTS.md`

阶段 1 不修改实现代码，不填写新 Workflow 的 Units。

### 阶段 2：Feature 实现

- 修改：`src/code_agent/**`
- 创建：`src/code_agent/workflows/**`

阶段 2 不修改 `code_agent_win/**`、根入口、README 或打包配置。

### 阶段 3：应用集成

- 修改：`code_agent_win/app.py`
- 修改：`code_agent_win/action_dispatcher.py`
- 修改：`code_agent_win/agent_modes.py`
- 修改：`code_agent_win/plugin_runtime.py`
- 修改：`code_agent_win/subagents.py`
- 修改：`code_agent_win/tools.py`
- 修改：根级 `tests/**`
- 修改：`README.md`

阶段 3 不再改变 `src/code_agent/**` 接口；若发现接口缺陷，停止集成并回到对应 Feature 阶段。

## 执行顺序

### Task 1: 冻结需求契约

- [ ] 更新九个现有 Feature 契约的目标和边界。
- [ ] 创建 `src/code_agent/workflows/AGENTS.md`，只写目标与边界。
- [ ] 运行契约检查：

```powershell
Get-ChildItem src\code_agent -Directory |
  Where-Object { Test-Path (Join-Path $_.FullName 'AGENTS.md') } |
  ForEach-Object { Get-Content -Raw (Join-Path $_.FullName 'AGENTS.md') | Out-Null }
```

预期：所有 Feature 只有一个可读取的 `AGENTS.md`，Workflow 契约不含 `## Units`。

### Task 2: 执行 Thread Intelligence 子计划

- [ ] 完成 Sessions schema、稳定 message sequence、`parent_thread_id`。
- [ ] 完成 ThreadAuthorization、semantic checkpoint/index Repository。
- [ ] 扩展 ContextBuilder 协议并完成 deterministic fallback。
- [ ] 完成 `search_threads`、`read_thread` Feature 服务。
- [ ] 运行子计划规定的全部测试。

### Task 3: 执行 Workflow 子计划

- [ ] 完成 Workflow models、DAG、持久化和可信事件投影。
- [ ] 完成 `/流程` 的 Host-neutral 命令与渲染能力。
- [ ] 运行 Workflow、Sessions、Orchestration、Verification、Interfaces 定向测试。

### Task 4: 执行 Plugin 子计划

- [ ] 完成 namespaced command、mode、custom agent。
- [ ] 完成 Event Proposal 执行协调和 Host Interaction。
- [ ] 完成 snapshot/revoke/digest 漂移测试。

### Task 5: 执行 Skills/MCP 子计划

- [ ] 完成 thread-scoped Skill activation persistence。
- [ ] 完成 `/技能`、`/mcp` Controller。
- [ ] 完成动态命令注册表、二级 Picker 和 MCP 动态工具刷新。
- [ ] 完成真实 stdio fixture 测试。

### Task 6: 阶段 2 完整验证

- [ ] 逐 Feature 运行：

```powershell
$testDirs = Get-ChildItem src\code_agent -Directory |
  ForEach-Object {
    $candidate = Join-Path $_.FullName 'tests'
    if (Test-Path $candidate) { $candidate }
  }
foreach ($dir in $testDirs) {
  python -m unittest discover -s $dir -p 'test_*.py' -q
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

预期：全部 Feature suite 通过；平台能力缺失只能产生已有显式 skip。

### Task 7: 根级应用集成

- [ ] 按四份子计划的 Integration Task 修改 `code_agent_win/**`。
- [ ] 保证 Dispatcher 从执行上下文绑定 caller thread。
- [ ] 保证 mode/MCP/Plugin snapshot 只在安全边界重建 runner。
- [ ] 保证主/子 Agent 共享持久 ThreadAuthorization 和 Workflow Service。
- [ ] 保证 TUI 统一使用动态命令注册事实。

### Task 8: 根级集成测试

- [ ] 先为每条端到端路径增加失败测试：

```text
long thread → semantic checkpoint → resume
parent → child → authorized search/read
three children → workflow branches → verification → delivery
plugin event → policy → interaction/action result
skill activate → resume → digest check
mcp enable → tool visible → disable → tool removed
```

- [ ] 运行：

```powershell
python -m unittest discover -s tests -p 'test_*.py' -v
```

预期：全部根级测试通过，无 traceback、未回收 task 或遗留 MCP 进程。

### Task 9: 完整回归与文档

- [ ] 运行 Task 6 和 Task 8。
- [ ] 运行：

```powershell
python -m build
```

预期：wheel 和 sdist 构建成功。

- [ ] 更新 README，只陈述已通过自动测试和人工验收的能力。
- [ ] 检查文档没有继续声称贡献“pending Host wiring”。

### Task 10: Windows Terminal 人工烟测

- [ ] 使用真实配置启动 `chaos-agent`。
- [ ] 验证 `/流程`、`/技能`、`/mcp` 和 namespaced Plugin command Picker。
- [ ] 验证 Plugin confirm/input/select 可见且 Esc 可取消。
- [ ] 验证一个超长线程触发 semantic checkpoint；摘要服务失败时任务仍继续。
- [ ] 验证三个并行子 Agent 形成三个 Workflow 分支。
- [ ] 关闭并恢复 TUI，确认 thread scope、Workflow 和 Skill activation 一致。
- [ ] 将命令、环境、结果和已知限制记录到新的 release validation 文档。

## 提交策略

每个提交只覆盖一个逻辑边界：

1. 需求契约。
2. Sessions/Thread Intelligence。
3. Workflow。
4. Plugin。
5. Skills/MCP。
6. 根级集成。
7. README 与验收记录。

精确暂存文件，不使用 `git add .`，不混入用户当前 `interfaces` 与 token-rate 改动。
