# ACP Editor Adapter
通过 ACP v1 stdio 把 Chaos Agent 的会话和事件流接入支持 Agent Client Protocol 的编辑器。

## 边界
- 负责：ACP v1 初始化、会话新建/加载、prompt、取消和 stdio 生命周期。
- 负责：把文本与 resource link 转为有界用户输入，把 `AgentEvent` 转为 ACP 文本、工具状态和终止原因。
- 负责：复用 Chaos Agent 现有会话 ID、Provider 配置和工具运行时；ACP 只是薄接口，不复制 Agent loop。
- 不负责：编辑器插件 UI、ACP v2 草案、客户端终端代理、未保存缓冲区同步、动态注入客户端 MCP server 或远程多用户服务。
- 首版必须与官方 Python SDK 的 ACP v1 类型和 stdio transport 对齐；对不支持的 content/capability 明确拒绝，不静默丢弃。

## Units
- `ChaosAcpAgent`: 实现 ACP v1 initialize/session/prompt/cancel 并复用现有 controller 与 sessions | 流式调用客户端 `session/update` | 单工作区、单 Agent loop 串行运行 prompt
- `acp_updates(event)`: 将模型文本、思考增量和工具生命周期事件映射为 ACP 更新 | 无副作用 | 不转发内部上下文和未受支持载荷
- `acp_stop_reason(event)`: 将 Chaos 终态映射为 ACP `stopReason` | 无副作用
