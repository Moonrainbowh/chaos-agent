# Runtime Intelligence、Workflow 与扩展闭环设计

## 状态

已于 2026-07-20 经用户确认。本文统一约束以下四个实施批次：

1. Thread Intelligence 与两级线程授权。
2. Workflow DAG 与 `/流程`。
3. Plugin 全贡献 Host 接线。
4. Skills 与 MCP 的统一 TUI 控制面。

四个批次在同一最终集成中交付，但 Feature 实现、测试和提交保持独立。

## 目标

- 让 Context Builder 使用真实 `thread_id` 和持久消息序号生成可追溯的语义 checkpoint。
- 持久化线程索引，并提供受线程树授权约束的 `search_threads`、`read_thread` 工具。
- 把真实 Core、Orchestration 和 Verification 生命周期投影为可恢复的 Workflow DAG。
- 让 Plugin 的 command、mode、agent、event 和 Host interaction 与现有 tool 一样进入真实运行链。
- 让用户通过统一命令注册表和 Picker 管理 Skills 与预配置 MCP server。
- 保持现有确定性压缩、中央策略、验证完成门和追加式 Windows Terminal 体验。

## 非目标

- 不实现通用 DAG 调度语言、鼠标画布或模型控制的可信工作流。
- 不允许子线程创建孙线程。
- 不自动 commit、push、创建 worktree 或恢复工作区代码。
- 不执行插件 Python、Shell callback、任意 URL 或动态进程内代码。
- 不允许 Skill 注册工具、启用 MCP 或扩大权限。
- 不通过 TUI 修改用户的 Provider、MCP 或插件信任配置文件。
- 不为旧线程伪造历史语义 checkpoint 或 Workflow。

## 已确认的产品决策

### 语义摘要模型

语义压缩使用当前任务冻结的模型和 Provider profile。摘要调用计入同一任务的 token、时间和恢复预算；恢复后继续使用持久化的冻结模式快照。

### 两级线程授权

`threads.parent_thread_id` 保存唯一父线程：

- 根线程的父 ID 为 `NULL`。
- 创建子线程时，父线程自身必须没有父线程。
- 父线程可读取自身和直接子线程。
- 子线程可读取自身和父线程。
- 子线程不可读取兄弟线程。
- Workflow Edge、Plugin 和模型参数均不能扩大该范围。

### Plugin 运行语义

- Event Action Proposal 进入中央 `ActionPolicy` 和 Dispatcher。
- `notify` 由 Host 直接显示；`confirm`、`input`、`select` 进入可取消的 `InteractionBroker`。
- Plugin command 始终使用 `/namespace.command`。
- Plugin mode 是附加的 namespaced mode，不能替换四个内置 mode。
- Plugin custom agent 通过 `delegate_agent.agent_id` 选择，并与 built-in `role` 互斥。

### 线程工具

`search_threads` 和 `read_thread` 是所有模式和 Agent 角色可见的只读工具。Host 从当前执行上下文绑定调用线程并计算授权范围；模型不能声明 caller 或任意扩大 scope。

## 总体架构

```text
可信持久层 Sessions
├── messages(sequence)
├── threads(parent_thread_id)
├── semantic_checkpoints
├── thread_index_entries
├── workflows / workflow_nodes / workflow_edges
└── skill_activations

运行服务层
├── ThreadAuthorization
├── ThreadIntelligenceService
├── WorkflowService
├── PluginRuntime
├── SkillController
└── McpController

执行层
├── ContextBuilder(thread_id, cancellation)
├── AgentEngine
├── SubagentRuntime
├── Verification
└── ActionDispatcher

表现层
├── /流程
├── /技能
├── /mcp
├── /namespace.command
└── Host InteractionBroker
```

边界规则：

- Sessions 保存事实，不推导权限或完成状态。
- ThreadAuthorization 只依据持久父子关系计算可见线程。
- Workflow Edge 只表达执行依赖或数据流。
- Thread Intelligence 的摘要和检索结果始终是不可信派生上下文。
- Plugin Proposal 必须经过 Host 投影、策略和交互边界。
- Interfaces 只渲染并委托控制器，不直接操作数据库、MCP 进程或插件动作。
- 根级 `code_agent_win` 只组合 Feature 公开接口，不重写 Feature 行为。

## Thread Intelligence

### Context Builder 协议

Context Builder 协议扩展为：

```python
async def build(
    thread_id: str,
    messages: Sequence[Message],
    user_input: str,
    tools: Sequence[ToolDefinition],
    task_state: TaskState,
    cancellation: CancellationToken,
) -> ContextBundle:
    ...
```

`messages` 保留用于 Core 的内存测试和当前消息流兼容。集成运行时通过受限的 Thread History Store 读取带真实数据库 sequence 的消息记录；不得使用列表下标生成持久 anchor。

### 持久化模型

`semantic_checkpoints` 至少保存：

- ID、thread ID。
- 来源起止 message sequence。
- 来源范围 digest。
- 摘要、生成模型、Usage 和格式版本。
- 创建时间。

`thread_index_entries` 至少保存：

- stable ID、thread ID、来源 sequence。
- source kind、content digest 和有界检索文本。
- relation、related stable ID 和创建时间。

checkpoint 与对应索引项在同一事务中发布。写入失败时，不得把未持久化摘要放入模型上下文。

### 压缩流程

1. Core 绑定当前 thread ID 和 CancellationToken。
2. Thread History Store 返回按 `messages.sequence` 排序的原始消息记录。
3. Context Engine 完成规则、工具、任务状态和消息压力测量。
4. 未达到 90% 压力阈值时，不调用摘要模型。
5. 达到阈值时，SemanticCompactor 只压缩闭合的旧消息块，并保留近期原文与完整工具调用/结果配对。
6. 当前冻结模型生成有界摘要；Usage 和耗时计入持久任务预算。
7. 持久化 checkpoint、索引项和脱敏数值事件。
8. 构造带 stable anchor 和 digest 的 developer checkpoint。

摘要超时、协议错误、格式错误、持久化错误或预算超限时继续使用现有 DeterministicCompactor。用户取消、父任务取消和 asyncio 取消继续向上传播，不作为普通摘要失败吞掉。

### 搜索与读取工具

`search_threads` 输入仅包含 query、limit 和可选 source kinds。授权线程集合由 Host 注入。

`read_thread` 以 stable anchor 为主键，返回有界原文、来源 digest 和后续 supersede/revert/tool-result 关系。它不接受“读取任意 thread 全文”的接口。

旧数据库迁移后采用懒索引：首次搜索或语义压缩时，从授权范围内的原始消息、事件、已持久 checkpoint 和有效 Evidence 增量补齐索引。

## Workflow DAG

### 定位

Workflow 是可信执行投影，不是模型预先生成的计划。只有具备独立目标、状态和产出的任务单元成为节点；文件读取、单次模型请求、普通日志和单次编辑保留为节点详情。

### 持久化

Sessions 增加：

- `workflows`
- `workflow_nodes`
- `workflow_edges`

WorkflowNode 保存 kind、title、status、assigned thread、role、输入/输出/Evidence 引用、工作区变更摘要和时间。WorkflowEdge 首版只支持 `requires`、`produces`、`review_of`。

### 可信事件投影

```text
TASK_CREATED              → 主任务节点
delegate_agent requested  → queued 子节点和依赖边
child running             → running
child terminal            → completed / failed / cancelled
completion candidate      → Verification 节点
verification outcome      → 有效 Evidence 绑定
task delivery             → 最终交付节点
approval required         → waiting_decision
```

Host Runtime 是节点可信状态的唯一写入者。模型文本和子 Agent advisory 不得直接生成完成节点或 Evidence。

### DAG 约束

- Edge 两端必须属于同一 Workflow。
- 拒绝自环、跨 Workflow Edge 和任意环路。
- 终态节点不可重新进入运行态。
- Evidence 失效后，执行节点可保留完成，但 Verification 和交付状态必须回退为未满足。
- Workflow Edge 不参与 thread 授权。
- 第一版只支持根线程和直接子线程。

### 恢复

Workflow 与任务状态一同加载。失效 owner 遗留的运行节点对账为 queued 或 cancelled，不重放命令。旧线程没有 Workflow 时不伪造历史，只从迁移后的真实动作开始记录。

### TUI

统一命令注册表增加：

```text
/流程
/流程 <node-id>
/流程 失败
/流程 证据 <node-id>
```

输出追加到普通 Windows Terminal 缓冲区。窄窗口隐藏次要字段，超大图折叠已完成分支，失败、阻塞和等待决策优先。所有不可信标题和输出经过现有终端控制序列过滤。

## Plugin Host 接线

### Tool

保留现有 Plugin Tool Bridge。Dispatcher 先评估 namespaced 插件动作，再评估映射后的 Host/MCP 动作；任一拒绝即失败闭合。

### Command

Plugin command 动态加入统一注册表、帮助和 Picker，公开名称始终为 `/namespace.command`。Host 构造有界 `PluginCommandInvocation`，只委托已注册的 task、session、review、workflow 等明确 Controller；不提供万能执行接口。

### Mode

Plugin mode 以 namespaced ID 作为内置 mode 的附加选择。它继承 `base_mode` 的冻结 profile、预算和权限，只能减少工具或降低 reasoning effort，且只在下一任务边界生效。

### Custom Agent

`delegate_agent` 增加可选 `agent_id`：

- `agent_id` 与 `role` 互斥。
- namespaced agent 继承其 base mode。
- 工具、写能力和 reasoning 只能保持或收紧。
- 继续使用父预算、中央策略、单写者锁、取消树和两级线程约束。

### Event 与 Interaction

```text
可信事件
→ 脱敏 EventProjection
→ DeclarativeEventRouter
→ 有界 Proposal
→ ActionPolicy / InteractionBroker
→ Dispatcher / 用户结果
→ 脱敏审计事件
```

单事件最多产生 32 个 Proposal，递归深度最多 2。插件不能抑制、修改或伪造原始事件。Action Proposal 的有效风险取插件声明和目标动作中的较高者。

`notify` 直接使用 Host 样式追加；`confirm`、`input`、`select` 必须保持可见、可取消，并由用户真实回答。交互结果不能直接成为完成证据。

### Snapshot 与撤销

新增贡献 snapshot 只在空闲或下一任务边界原子应用。revoke 立即阻止新调用。digest 漂移后插件失去信任，旧贡献不能继续运行。激活、拒绝、交互和 Proposal 结果均写入脱敏审计事件。

## Skills TUI

统一命令注册表增加：

```text
/技能 列表 [--all|--active|--errors]
/技能 信息 <id>
/技能 启用 <id>
/技能 禁用 <id>
/技能 来源 <id>
/技能 重载
```

用户级 Skill 可在当前会话激活。工作区 Skill 首次激活必须显式确认。Sessions 只持久化 thread ID、Skill ID、来源和 digest；恢复时 digest 一致才重新激活，变化后重新确认。

冲突、损坏或越界 Skill 单独隔离。Skill 只能增加有界上下文，不能注册工具、执行脚本、启用 MCP 或修改策略。

## MCP TUI

统一命令注册表增加：

```text
/mcp 列表
/mcp 状态 [server]
/mcp 工具 <server>
/mcp 启用 <server>
/mcp 禁用 <server>
/mcp 重启 <server>
/mcp 诊断 <server>
```

只允许操作预配置且已批准的 server。启用、禁用是当前应用会话状态，不反写用户配置。完成 SDK handshake 和 tool schema 校验后，工具才进入下一模型回合；禁用或崩溃后立即撤下工具并有界取消 in-flight call。

诊断只显示健康状态、工具数量、脱敏有界 stderr 和错误类别。所有 MCP 调用继续使用本地风险映射、ActionPolicy、审批、取消、超时和审计。

## 统一命令与 Picker

内置命令、Plugin command、Skills、MCP、mode、session 和 Workflow 使用同一动态注册表：

- 一级选择有动作时进入二级 Picker。
- 叶子动作一次 Enter 执行。
- 禁用项保留可见原因。
- 动态 snapshot 在下一次打开 Picker 时刷新，不在一次选择过程中替换候选。
- 命令解析、帮助、补全、Picker 和运行委托来自同一注册事实。

## 数据库迁移

迁移必须从当前 schema 版本顺序升级，并至少覆盖：

- `threads.parent_thread_id` 及索引。
- semantic checkpoint 和 thread index 表。
- Workflow、Node、Edge 表及约束索引。
- thread-scoped Skill activation 表。

迁移使用现有 exclusive transaction、quick check、foreign-key check 和 required-column validation。旧数据库没有新记录时仍可正常恢复，不创建伪事实。

## 错误处理

- Semantic 服务失败：确定性压缩。
- Semantic 持久化失败：丢弃本次语义摘要，不使用半成品。
- Thread 越权：返回结构化拒绝，不泄漏目标是否存在。
- DAG 环路或非法转换：拒绝并记录结构化错误事件。
- Workflow 持久化失败：任务不得报告未持久化节点为可信完成。
- Plugin Proposal 失败：隔离该 Proposal，不修改原始事件。
- Plugin UI 取消：返回明确 cancelled 结果。
- Skill digest 漂移：停用并要求重新确认。
- MCP 启动、schema 或健康检查失败：撤下工具、关闭进程并显示脱敏错误类别。
- TUI 渲染失败：降级为有界文本列表，不影响 Core 执行。

## 分阶段实施

### 阶段 1：需求契约

更新现有 Feature AGENTS，并创建 Workflow Feature AGENTS。只定义目标、边界和跨 Feature 约定，不编写实现。

### 阶段 2：Feature 实现

按以下顺序执行 TDD：

1. Sessions schema、sequenced messages、parent thread 和新记录 Repository。
2. ThreadAuthorization、语义持久化服务和搜索/读取服务。
3. Workflow models、DAG、service、renderer 和命令解析。
4. Plugin command/mode/agent/event/interaction Host-neutral 能力。
5. Skills/MCP Controller 与动态 Picker 能力。

Feature 阶段不修改根级 `code_agent_win`。

### 阶段 3：应用集成

在 `code_agent_win` 中：

- 把 thread ID 和 cancellation 传入 Context Builder。
- 使用当前冻结 profile 构造摘要服务并累计任务 Usage。
- 创建子线程时持久化 parent thread。
- 注册 `search_threads`、`read_thread`。
- 创建并订阅 Workflow Service。
- 连接全部 Plugin contribution 和 InteractionBroker。
- 连接 `/流程`、`/技能`、`/mcp` 与动态 Plugin command。
- 在 mode 切换、MCP 工具变化和 Plugin snapshot 边界重建下一任务运行时。

### 阶段 4：验证与交付

- 运行每个 Feature 的定向测试。
- 运行根级集成测试。
- 运行完整自动化回归。
- 使用真实 SDK fixture 验证 MCP stdio 生命周期。
- 在 Windows Terminal 手工验证命令 Picker、审批、Plugin interaction、并行 Workflow、关闭恢复和超长线程压缩。

## 测试矩阵

### Sessions 与 Thread Intelligence

- 旧数据库迁移和完整性检查。
- 稳定 message sequence 往返。
- 两级线程创建及孙线程拒绝。
- 父、子、兄弟和无关线程授权。
- 语义阈值、成功持久化、Usage 计费和 deterministic fallback。
- checkpoint/index 原子发布。
- search/read 的 scope、limit、anchor digest 和 revision 关系。

### Workflow

- 顺序链、并行分支和汇合。
- 自环、多节点环和跨 Workflow 拒绝。
- 合法与非法状态转换。
- Subagent 生命周期投影和父取消传播。
- Verification Evidence 绑定及失效回退。
- 中断恢复且不重放命令。
- `/流程` 快照、详情、失败过滤、Evidence 和窄窗口降级。

### Plugin

- Tool 双重风险保持。
- namespaced command 注册、帮助、Picker 和 Controller 委托。
- namespaced mode 只收紧能力。
- custom agent 选择、预算、授权和禁止孙线程。
- Event 脱敏、Proposal 上限、递归限制和策略路径。
- notify/confirm/input/select 的显示、取消和真实回答。
- snapshot、revoke、digest 漂移和故障隔离。

### Skills 与 MCP

- Skill 列表、信息、来源、启用、禁用、重载和冲突。
- 工作区 Skill 确认、持久激活、恢复和 digest 漂移。
- MCP 列表、状态、工具、启用、禁用、重启和诊断。
- 真实 stdio handshake、tool call、取消、超时、崩溃和干净关闭。
- 动态工具变化在下一模型回合生效。
- 统一 Picker 的二级动作、禁用原因和 snapshot 稳定性。

### 完整回归

现有根级集成测试和所有 Feature 测试必须继续通过。新增测试不得依赖真实外部网络或密钥；真实 Provider 人工烟测单独记录，不写入自动测试。

## 完成定义

- 超长主线程和子线程能生成、持久化、恢复可追溯 semantic checkpoint。
- `search_threads`、`read_thread` 在父子范围内可用，兄弟和无关线程不可见。
- 多 Agent 任务能通过 `/流程` 展示真实、持久、可恢复的分支与 Evidence。
- Plugin 的 tool、command、mode、agent、event 和 interaction 全部进入 Host 运行链且不可绕过策略。
- Skills 和 MCP 可通过统一 TUI 命令及 Picker 管理。
- 语义、Workflow、Plugin、Skill 或 MCP 的局部失败不会破坏现有确定性上下文、任务恢复或安全完成门。
