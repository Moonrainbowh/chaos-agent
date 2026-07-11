# Agent Core
以有界、可取消的事件循环协调模型、上下文和工具动作，形成与界面无关的编码 Agent 内核。

## 边界
- 负责：回合状态机、工具调用编排、停止条件、错误回送、事件发布和验证结果闭环。
- 负责：对同一持久任务累计模型回合和工具调用，并执行每回合工具限制。
- 负责：通过抽象协议调用模型、上下文、动作分发与会话能力，不依赖具体实现。
- 不负责：直接访问模型 API、文件系统、子进程、数据库或终端界面。
- 不负责：绕过权限策略执行任何外部动作。

## Units
- `Message`、`ToolCall`: 表达对话内容与模型工具调用 | 无副作用 | 输入在构造时校验并冻结
- `ActionRequest`、`ActionResult`、`ToolDefinition`: 定义动作请求、结果与工具元数据 | 无副作用 | 仅承载 JSON 兼容数据
- `ModelEvent`、`Usage`、`ContextBundle`: 表达模型流事件、用量和构建后的上下文 | 无副作用
- `AgentEvent`: 发布可持久化的内核生命周期事件 | 生成 UTC 时间戳
- `CancellationToken`、`CancellationError`: 在线程与异步调用间传播首次取消原因 | 唤醒等待者
- `ModelClient`: 约束统一的模型流式调用接口 | 具体副作用由实现负责
- `ContextBuilder`: 异步构建当前回合上下文 | 具体副作用由实现负责
- `ActionDispatcher`: 暴露工具并分发可取消动作 | 具体副作用由实现负责
- `SessionRepository`: 异步创建线程并持久化消息与事件 | 具体副作用由实现负责
- `EngineLimits`: 冻结模型回合、工具调用、token 与输出字符预算 | 无副作用 | 越界前先阻止新的外部工具动作
- `TaskBudget`: 表达可恢复任务的模型名、限制和已消耗额度 | 无副作用 | 只允许单调增加的使用量
- `AgentEngine.run(user_input, thread_id, cancellation)`: 持久化并流式发布回合、模型、工具和终态事件 | 调用抽象模型、动作与会话协议 | 未声明工具、重复调用 ID、无完成事件和预算越界均失败闭合
- `SessionJournal`: 把会话协议异常转换为稳定的内核持久化错误 | 调用会话协议 | 不允许不可信历史消息进入上下文
- `AgentEngineError` 及子类: 表达预算、模型流、上下文构建与持久化失败 | 无副作用 | 对外错误不包含上游异常文本
