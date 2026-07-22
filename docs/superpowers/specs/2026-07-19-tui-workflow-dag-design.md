# Chaos Agent TUI 节点式工作流设计

## 目标

为 Chaos Agent 增加终端任务流程视图：

- 普通问答继续使用线性聊天。
- 单 Agent 长任务可显示顺序执行链。
- 多 Agent 任务自动显示分支、并行和汇合。
- 用户通过 `/流程` 查看实际执行结构。
- 工作流节点必须来自真实运行状态，不能由模型文本伪造。
- 工作流、父子线程和 Git 历史保持独立，通过稳定 ID 关联。

第一版的成功定义是：

> 多 Agent 任务发生时，用户可以在不离开线性聊天的情况下，通过 `/流程` 查看真实、持久、可恢复、不可伪造的任务分支、执行状态、汇合关系和验证证据。

## 非目标

第一版不实现：

- 鼠标拖拽或图形化节点画布。
- 用户任意连接、删除或重排运行中的节点。
- 由模型文本直接创建可信完成状态。
- 默认全屏 Dashboard。
- 通用 DAG 调度语言。
- 自动 Git commit、push 或 worktree。
- 子线程继续创建孙线程。

## 核心产品决策

### 线性聊天是默认界面

默认继续使用现有追加式聊天转录：

```text
› 请检查登录模块并修复问题

◆ 我会并行检查调用链、历史实现和安全边界。

↳ Search Agent 已启动
↳ Librarian Agent 已启动
↳ Review Agent 已启动
```

用户输入 `/流程` 后，输出当前执行流程的只读快照：

```text
任务：修复登录超时问题

● 1 主任务分析             main
├─✓ 2 搜索调用链           search       8s
├─✓ 3 查询历史实现         librarian   11s
├─● 4 安全审查             review      15s
└─○ 5 汇总并修改           main        blocked
   └─○ 6 运行验证           verifier    waiting
      └─○ 7 交付结果        main        waiting
```

### 节点代表任务单元

只有具备独立目标、状态和产出的任务单元才能成为节点：

- 主任务分析。
- 子 Agent 调研。
- 代码实现。
- Verification。
- Review。
- 用户审批。
- 最终交付。

以下动作不单独成为节点，而是记录在所属节点的详情中：

- `read_file`。
- `search_text`。
- 单次模型请求。
- 普通日志。
- 单次文件编辑。

### 第一版是执行投影

第一版不让模型预先生成并控制完整 DAG。Workflow Service 根据真实的 Core、Orchestration 和 Verification 事件逐步创建及更新节点。

这样可以确保流程图表达的是实际发生的工作，而不是模型对工作过程的叙述。

## 三类关系

### Workflow DAG

Workflow DAG 负责：

- 任务节点。
- 前置依赖。
- 并行分支。
- 汇合关系。
- 节点状态。
- 输入、输出和 Evidence 引用。

Workflow Edge 只表达执行依赖或数据流，不授予线程权限。

### Thread Parent/Child

Thread Parent/Child 负责：

- 父 Agent 与子 Agent 关系。
- 读取授权。
- 预算租借。
- 取消传播。
- 生命周期。

当前只支持两级：

```text
父线程
├── 子线程 A
├── 子线程 B
└── 子线程 C
```

禁止子线程继续创建孙线程。

### Git Graph

Git Graph 负责：

- 代码状态。
- Diff。
- 工作区恢复。
- 可选 checkpoint。
- 后续 rewind。

一个 Workflow Node 可以关联执行线程和 Git checkpoint，但三者不是同一个对象。

## 架构

新增 Workflow Feature：

```text
src/code_agent/workflows/
├── AGENTS.md
├── models.py
├── graph.py
├── service.py
└── tests/
```

各模块职责：

- `models.py`：定义 Workflow、Node、Edge、状态及不可变快照。
- `graph.py`：维护 DAG 约束、拓扑关系和合法状态转换。
- `service.py`：接收可信运行事件并更新流程。
- `sessions`：持久化 Workflow、Node 和 Edge。
- `orchestration`：把子 Agent 生命周期投影为节点状态。
- `verification`：把 Evidence 绑定到验证节点。
- `interfaces`：渲染流程快照、节点详情和命令结果。
- `code_agent_win`：组合 Workflow Service 与现有 TUI、任务和子 Agent Runtime。

## 数据模型

### WorkflowNodeStatus

```python
class WorkflowNodeStatus(str, Enum):
    PLANNED = "planned"
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    WAITING_DECISION = "waiting_decision"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

### WorkflowNode

```python
@dataclass(frozen=True)
class WorkflowNode:
    id: str
    workflow_id: str
    kind: str
    title: str
    status: WorkflowNodeStatus
    assigned_thread_id: str | None
    role: str
    input_refs: tuple[str, ...]
    output_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    git_checkpoint: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
```

### WorkflowEdge

```python
@dataclass(frozen=True)
class WorkflowEdge:
    workflow_id: str
    source_node_id: str
    target_node_id: str
    kind: str
```

首版 Edge Kind 固定为：

- `requires`：目标节点必须等待来源节点。
- `produces`：来源节点向目标节点提供输出。
- `review_of`：来源节点审查目标节点对应的工作。

### 约束

- 不允许节点指向自身。
- 不允许形成循环。
- Edge 两端必须属于同一个 Workflow。
- 终态节点不可重新进入 `RUNNING`。
- 只有 Host Runtime 可以更新节点可信状态。
- 模型只能提出节点建议，不能直接写入完成状态。
- Evidence 只能引用 Verification 产生的有效证据。
- Workflow Edge 不得扩大线程授权。

## 工作流生命周期

```text
用户创建任务
  → 创建主任务节点

主 Agent 调用 delegate_agent
  → 创建子 Agent 节点
  → 建立主任务到子任务的 Edge

多个子 Agent 启动
  → 对应节点进入 running
  → TUI 显示并行分支

子 Agent 完成
  → 节点记录有界输出引用
  → 节点进入 completed

父 Agent 开始汇总
  → 创建或启动汇总节点

Agent 提出完成候选
  → 创建 Verification 节点

验证通过
  → 绑定 Evidence
  → 创建最终交付节点
```

建议状态转换：

```text
planned → queued → running
running → completed
running → failed
running → cancelled
queued → cancelled
running → waiting_decision → running
completed → verifying → completed
```

不合法转换必须失败闭合，不得静默修正。

## 父子线程授权

在 `threads` 增加可空字段：

```text
parent_thread_id
```

规则：

- 父线程的 `parent_thread_id` 为 `NULL`。
- 子线程指向唯一父线程。
- 创建子线程时，指定父线程自身必须没有父线程。
- 子线程禁止创建新的子线程。
- 父线程可以读取自身及直接子线程。
- 子线程默认只读取自身和父线程。
- 子线程默认不能读取兄弟线程。
- 父 Agent 汇总时，由 Host 把其他子线程的有界结果传给父线程。
- Workflow Edge 不参与权限判断。

## 持久化

在 Sessions 数据库增加：

```text
workflows
workflow_nodes
workflow_edges
```

关键状态不从 UI 文本动态重建。

恢复时：

1. 读取任务及线程状态。
2. 读取 Workflow、Node 和 Edge。
3. 将失效执行所有者遗留的 `RUNNING` 节点对账为 `INTERRUPTED` 或 `QUEUED`。
4. 不自动重放中断中的命令。
5. 根据持久化节点重新生成 `/流程` 输出。
6. 校验节点引用的线程仍属于当前两级父子关系。

数据库迁移必须兼容没有 Workflow 记录的旧线程。旧线程恢复时不伪造历史流程，只在后续真实动作发生时创建新 Workflow。

## TUI 设计

### 状态符号

```text
○ waiting
● running
✓ completed
× failed
! waiting decision
```

### 命令

新增一级命令：

```text
/流程
/flow
```

首版支持：

```text
/流程
```

显示当前任务的紧凑 DAG。

```text
/流程 <node-id>
```

显示节点详情：

```text
节点 4 · 安全审查

状态：running
角色：Review Agent
线程：thread-a8f2
依赖：节点 1
耗时：15s
工具调用：6
输入：登录模块、相关 Diff
输出：尚未完成
证据：无
```

```text
/流程 失败
```

只显示失败、阻塞和等待决策的节点。

```text
/流程 证据 <node-id>
```

显示节点关联的有效 Evidence。

### 渲染规则

- 保持追加式 Windows Terminal 设计。
- `/流程` 输出一个稳定快照，不接管终端滚动历史。
- 窄窗口按优先级隐藏耗时、角色和用量，保留节点标题与状态。
- 节点数量过多时默认折叠已完成分支。
- 失败、阻塞和等待审批状态优先显示。
- 所有不可信文本先经过现有终端安全过滤。
- 模型文本不得提供 ANSI 样式或状态符号。

## Git 集成路线

### 第一阶段

Workflow Node 只关联：

```text
changed_paths
diff_digest
subject_generation
```

### 第二阶段

增加：

```text
git_checkpoint
workspace_snapshot_id
```

支持：

```text
/流程 diff <node-id>
```

查看某个节点产生的修改。

### 第三阶段

在用户明确批准后支持：

```text
/流程 恢复 <node-id>
```

恢复某个节点前的代码状态。

第一版不得自动 commit、push 或创建 worktree。

## 错误处理

- DAG 环路：拒绝新 Edge，并记录结构化错误事件。
- 丢失父节点：拒绝创建 Edge，不创建孤立的可信完成节点。
- 子 Agent 异常退出：对应节点进入 `FAILED` 或 `CANCELLED`。
- Workflow 持久化失败：任务不得把未持久化节点报告为可信完成。
- Evidence 失效：节点保留执行完成状态，但验证状态必须回退为未满足。
- Thread 授权失效：隐藏相关输出并拒绝 `read_thread`。
- TUI 渲染失败：降级为有界线性节点列表，不影响 Core 执行。

## 测试策略

### Workflow Feature

- 创建合法顺序链。
- 创建合法并行分支与汇合。
- 拒绝自环。
- 拒绝多节点环。
- 拒绝跨 Workflow Edge。
- 验证所有合法与非法状态转换。
- 验证终态不可重新运行。

### Sessions

- Workflow、Node、Edge 往返持久化。
- 并发写入保持一致。
- 旧数据库迁移。
- 中断节点恢复对账。
- 不重放中断命令。

### Orchestration

- `delegate_agent` 创建子节点。
- queued、running 和终态准确投影。
- 三个并行子 Agent 形成三个分支。
- 父取消传播为子节点取消。
- 子 Agent 不能创建孙线程。

### Verification

- 完成候选创建验证节点。
- 有效 Evidence 可绑定节点。
- 失效 Evidence 不显示为通过。
- 子 Agent 建议不能作为 Evidence。

### Interfaces

- `/流程` 渲染顺序链。
- `/流程` 渲染并行分支。
- 节点详情正确显示。
- 窄窗口降级。
- 超大 Workflow 有界折叠。
- 不可信文本无法注入 ANSI。

### 集成

- 简单问答保持原有线性体验。
- 单 Agent 长任务显示顺序链。
- 多 Agent 任务显示分支与汇合。
- 关闭并恢复 TUI 后 Workflow 结构一致。
- 无有效 Evidence 时验证节点不得显示成功。

## 实施阶段

### 阶段 1：需求契约

创建或更新：

```text
src/code_agent/workflows/AGENTS.md
src/code_agent/orchestration/AGENTS.md
src/code_agent/interfaces/AGENTS.md
src/code_agent/sessions/AGENTS.md
```

明确：

- Workflow 不负责权限。
- Thread 不负责数据依赖。
- Git 不负责任务状态。
- 第一版是执行投影，不是通用可视化编排器。

### 阶段 2：Feature 实现

依次实现：

1. Workflow Models。
2. DAG 校验与状态转换。
3. Workflow Repository。
4. Orchestration Event Projection。
5. Workflow Terminal Renderer。
6. `/流程` 命令解析。
7. Node Detail 和 Evidence 查询。

每个 Feature 独立测试，集成代码不得进入 Feature 目录。

### 阶段 3：应用集成

在 Windows 应用组合层：

- 创建 Workflow Service。
- 将主任务绑定 Workflow。
- 将 Subagent 生命周期接入 Workflow。
- 将 Verification Evidence 绑定节点。
- 把 `/流程` 接入 TUI。
- 恢复时加载持久化 Workflow。

## 验收标准

### 简单任务

普通问答不显示多余流程节点，聊天体验保持不变。

### 单 Agent 长任务

```text
分析 → 修改 → 验证 → 交付
```

可以显示为顺序链。

### 多 Agent 任务

三个子 Agent 并行时，流程必须显示三个分支，并在父 Agent 汇总节点汇合。

### 安全性

- 模型文本不能把节点标记为 `COMPLETED`。
- 子线程不能创建孙线程。
- 子线程不能读取兄弟线程。
- Workflow Edge 不能扩大权限。
- 无有效 Evidence 时，Verification 节点不能显示成功。

### 恢复

关闭并重新打开 TUI 后：

- Workflow 结构保持一致。
- 已完成节点保持完成。
- 中断节点不会重放命令。
- `/流程` 能恢复原来的执行视图。

## 推荐实施顺序

1. 持久化 Workflow 数据模型。
2. Subagent 生命周期投影。
3. `/流程` 只读 TUI。
4. Verification 与 Evidence 关联。
5. 恢复和中断对账。
6. 节点级 Diff。
7. Workspace Rewind。
8. 最后再评估通用 DAG 调度器和图形界面。

