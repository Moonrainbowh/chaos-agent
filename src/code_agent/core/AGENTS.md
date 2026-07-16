# Agent Core
以有界、可取消的事件循环协调模型、上下文和工具动作，形成与界面无关的编码 Agent 内核。

## 边界
- 负责：回合状态机、工具调用编排、停止条件、错误回送、事件发布和验证结果闭环。
- 负责：对同一持久任务累计模型回合和工具调用，并执行每回合工具限制。
- 负责：通过抽象协议调用模型、上下文、动作分发与会话能力，不依赖具体实现。
- 负责：以不可变 `ContextRequest` 携带真实 `thread_id` 与正数 `revision`。
- 不负责：直接访问模型 API、文件系统、子进程、数据库或终端界面。
- 不负责：绕过权限策略执行任何外部动作。
- 任何模型调用或外部动作前必须读取恢复后的持久预算与控制状态；已耗尽任务必须零 provider、零工具调用地进入稳定暂停状态。
- `COMPLETED` 只能由最新 contract、subject generation 和 required evidence 的系统评估产生；模型文本或 completion candidate 不能直接完成任务。
- 负责协调抽象 `VerificationService` 的完成评估与 `VERIFYING` 状态；不导入具体 verification 或 projects adapter。

## Units
- `TaskIntent`、`AcceptanceCriterion`、`TaskContractRevision`: 表达不可降级的完成条件与 revision | 无副作用 | 不写入旧 `core/models.py`
- `ActionEffect`、`CompletionCandidate`、`CompletionAssessment`、`assess_completion(...)`: 以 generation/subject/evidence 纯函数评估 verified、partial 或 unverified | 无副作用 | 模型文本不能生成通过证据
- `VerificationService`: 约束 core 请求抽象验证与完成候选 | 具体副作用由实现负责 | core 不导入 verification 或 projects adapter
- `VerificationAssessment`、`TaskVerificationService`: 将当前 subject 的 assessment、verifier outcome 和原子完成句柄传回 core | 具体副作用由实现负责 | engine 只调用抽象协议，不能自行伪造 evidence
- `TaskVerificationService.suggest_verification(...)`: 在没有当前测试 evidence 时返回一个受信的 typed verifier tool call | 具体 recipe 由实现选择 | 不能包含 shell、argv 或安装参数；系统调用仍须持久化配对的 assistant tool-call 消息
- `AgentEngineCompletionMixin._resolve_task_completion(...)`: 将模型停调用后的 assessment 交给持久验证门 | 调用抽象验证与 sessions 协议 | 只有 sessions 原子 finalize 可产生 `COMPLETED`
- `decide_verification_transition(...)`: 将 assessment 与 verifier outcome 映射为 `VERIFYING`、修复、等待或完成 | 无副作用 | 所有状态先持久化再由集成层发布
- `Message`、`ToolCall`: 表达对话内容与模型工具调用 | 无副作用 | 输入在构造时校验并冻结
- `ContextRequest`: 以不可变快照携带单次上下文构建的 thread、revision、输入、控制与纯数值预算 | 无副作用 | JSON 快照深复制并冻结
- `ActionRequest`、`ActionResult`、`ToolDefinition`: 定义动作请求、结果与工具元数据 | 无副作用 | 仅承载 JSON 兼容数据
- `ModelEvent`、`Usage`、`ContextBundle`: 表达模型流事件、用量和构建后的上下文 | 无副作用 | 上下文度量只允许固定名称的非负整数计数
- `AgentEvent`: 发布可持久化的内核生命周期事件 | 生成 UTC 时间戳 | `CONTEXT_BUILT` 仅记录本地数值预算、压缩和缓存计数，不含提示或工具输出
- `CancellationToken`、`CancellationError`: 在线程与异步调用间传播首次取消原因 | 唤醒等待者
- `ModelClient`: 约束统一的模型流式调用接口 | 具体副作用由实现负责
- `ContextBuilder`: 异步构建当前回合上下文 | 具体副作用由实现负责
- `CommandFact`、`TaskState`、`TaskStateUpdate`、`reduce_task_state`: 以有界 JSON 兼容事实表达持久任务进度 | 无副作用 | 工作笔记始终显式标注为未验证
- `ActionDispatcher`: 暴露工具并分发可取消动作 | 具体副作用由实现负责
- `SessionRepository`: 异步创建线程并持久化消息与事件 | 具体副作用由实现负责
- `EngineLimits`: 冻结模型回合、工具调用、token 与输出字符预算 | 无副作用 | 越界前先阻止新的外部工具动作
- `TaskBudget`: 表达可恢复任务的模型名、限制和已消耗额度 | 无副作用 | 只允许单调增加的使用量
- `TaskAuthorization`、`TaskContract`、`TaskRecord`、`TaskStatus`: 表达前台自主任务的范围、预算和生命周期 | 无副作用 | `ACCEPTED_PARTIAL` 只能由显式用户决定产生，不计为 verified completion
- `TaskSupervisor.observe(...)`: 根据恢复后的持久预算、验证结果和失败指纹决定继续、checkpoint、暂停或等待决策 | 无副作用 | 累计活跃时间、重复失败和修复循环不依赖进程内状态
- `AgentEngine.run(..., task=...)`: 在同一 thread 内执行一个显式任务并持久化任务事件 | 调用抽象模型、动作与会话协议 | 在安全边界消费 steering，绝不重放中断中的命令；自动验证通过后由持久完成门直接收尾
- `AgentEngine.run(user_input, thread_id, cancellation)`: 持久化并流式发布回合、模型、工具和终态事件 | 调用抽象模型、动作与会话协议 | 未声明工具、重复调用 ID、无完成事件和预算越界均失败闭合
- `SessionJournal`: 把会话协议异常转换为稳定的内核持久化错误 | 调用会话协议 | 不允许不可信历史消息进入上下文
- `AgentEngineError` 及子类: 表达预算、模型流、上下文构建与持久化失败 | 无副作用 | 对外错误不包含上游异常文本
