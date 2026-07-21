# Workflow DAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> 实施状态（2026-07-20）：持久 DAG、可信 Host 投影、恢复与 `/流程` 已完成自动化实现和测试；三并行分支的真实 TUI 人工验收仍归总计划 Task 9。

**Goal:** 将真实任务、子 Agent、验证和交付生命周期持久化为不可伪造的 Workflow DAG，并通过 `/流程` 提供可恢复只读视图。

**Architecture:** Workflow Feature 维护模型、DAG 约束和可信事件投影；Sessions 实现持久化；Interfaces 提供 Host-neutral renderer。根级集成订阅 Core、Subagent 和 Verification 事件，不允许模型直接写状态。

**Tech Stack:** Python 3.10、SQLite、unittest、Windows Terminal renderer。

---

### Task 1: 创建 Workflow 契约

**Files:**
- Create: `src/code_agent/workflows/AGENTS.md`
- Modify: `src/code_agent/orchestration/AGENTS.md`
- Modify: `src/code_agent/interfaces/AGENTS.md`
- Modify: `src/code_agent/sessions/AGENTS.md`

- [ ] 只写目标、边界、三图分离、可信 Host 写入、禁止孙线程和错误降级。
- [ ] 确认新契约没有 Units。

### Task 2: Workflow models

**Files:**
- Create: `src/code_agent/workflows/models.py`
- Create: `src/code_agent/workflows/tests/test_models.py`

- [ ] **Step 1: 写失败构造测试**

覆盖 `WorkflowNodeStatus`、`WorkflowNode`、`WorkflowEdge`、时间顺序、有界 refs 和不可变 tuple。

- [ ] **Step 2: 实现最小 dataclass 与 enum**
- [ ] **Step 3: 运行 `test_models.py`**

### Task 3: DAG 与状态转换

**Files:**
- Create: `src/code_agent/workflows/graph.py`
- Create: `src/code_agent/workflows/tests/test_graph.py`

- [ ] **Step 1: 写顺序链、并行分支、汇合测试**
- [ ] **Step 2: 写自环、跨 Workflow、多节点环失败测试**
- [ ] **Step 3: 写终态不可重启和非法转换失败测试**
- [ ] **Step 4: 实现 `WorkflowGraph.add_node/add_edge/transition/snapshot`**

环检测使用有界 DFS，只遍历当前 Workflow：

```python
if source == target or self._reachable(target, source):
    raise WorkflowCycleError("workflow edge would create a cycle")
```

- [ ] **Step 5: 运行 graph 测试**
- [ ] **Step 6: 更新 Workflow AGENTS Units**

### Task 4: Sessions Workflow migration

**Files:**
- Modify: `src/code_agent/sessions/_database.py`
- Create: `src/code_agent/sessions/_workflows.py`
- Modify: `src/code_agent/sessions/repository.py`
- Modify: `src/code_agent/sessions/tests/test_migrations.py`
- Create: `src/code_agent/sessions/tests/test_workflows.py`

- [ ] **Step 1: 写 schema 10→11 失败迁移测试**
- [ ] **Step 2: 增加 workflows、workflow_nodes、workflow_edges 与索引**
- [ ] **Step 3: 写 Workflow/Node/Edge 原子往返测试**
- [ ] **Step 4: 写并发 edge 插入和 FK 回滚测试**
- [ ] **Step 5: 实现 Repository mixin**
- [ ] **Step 6: 运行 Sessions 全套测试**

### Task 5: 可信 Workflow Service

**Files:**
- Create: `src/code_agent/workflows/service.py`
- Create: `src/code_agent/workflows/tests/test_service.py`

- [ ] **Step 1: 写 TASK_CREATED→主节点失败测试**
- [ ] **Step 2: 写 child queued/running/terminal 投影测试**
- [ ] **Step 3: 写 completion candidate→verification→delivery 测试**
- [ ] **Step 4: 写 Evidence 失效回退测试**
- [ ] **Step 5: 写持久化失败时不发布可信完成测试**
- [ ] **Step 6: 实现只接受 typed Host observation 的 `WorkflowService`**

服务不接受任意模型 dict；入口类型固定为 Host 定义的 observation dataclass。

- [ ] **Step 7: 运行 Workflow 测试**

### Task 6: 恢复对账

**Files:**
- Modify: `src/code_agent/workflows/service.py`
- Modify: `src/code_agent/sessions/_task_execution.py`
- Modify: `src/code_agent/workflows/tests/test_service.py`
- Modify: `src/code_agent/sessions/tests/test_repository.py`

- [ ] **Step 1: 写失效 owner 遗留 RUNNING 节点测试**
- [ ] **Step 2: 断言恢复为 QUEUED/CANCELLED 且命令不重放**
- [ ] **Step 3: 实现幂等 reconciliation**
- [ ] **Step 4: 运行恢复测试**

### Task 7: Workflow renderer 与命令

**Files:**
- Create: `src/code_agent/interfaces/workflow_view.py`
- Modify: `src/code_agent/interfaces/command_registry.py`
- Modify: `src/code_agent/interfaces/tui_commands.py`
- Create: `src/code_agent/interfaces/tests/test_workflow_view.py`
- Modify: `src/code_agent/interfaces/tests/test_tui_commands.py`

- [ ] **Step 1: 写 `/流程`、详情、失败过滤、Evidence 解析失败测试**
- [ ] **Step 2: 写顺序链、三分支、窄窗口和大图折叠渲染测试**
- [ ] **Step 3: 实现 `WorkflowView.render(snapshot, width, filter)`**
- [ ] **Step 4: 所有 title/ref 经过 `safe_text`**
- [ ] **Step 5: 运行 Interfaces 测试**

### Task 8: 根级 Workflow 集成

**Files:**
- Modify: `code_agent_win/app.py`
- Modify: `code_agent_win/app_ui.py`
- Modify: `code_agent_win/subagents.py`
- Modify: `tests/test_agent_app.py`
- Modify: `tests/test_subagent_integration.py`

- [ ] **Step 1: 写三子 Agent 分支失败集成测试**
- [ ] **Step 2: 写 Verification/Evidence/交付失败集成测试**
- [ ] **Step 3: 写关闭恢复 `/流程` 一致性测试**
- [ ] **Step 4: 创建共享 WorkflowService 并订阅真实生命周期**
- [ ] **Step 5: TUI `/流程` 只读取 snapshot，不重建状态**
- [ ] **Step 6: 运行根级和五个相关 Feature suite**
