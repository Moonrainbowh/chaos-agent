# ACP Editor Adapter
通过 ACP v1 stdio 把 Chaos Agent 的会话和事件流接入支持 Agent Client Protocol 的编辑器。

## 边界
- S8：prompt 必须经过共享持久任务服务，使用实际冻结 Task/执行 owner/恢复门/累计预算/结果契约；ACP session ID 可投影当前任务消息线程，但不能直接调用无 Task 的 AgentController.ask。终态连续对话新建继承消息任务，未终态恢复原任务；临时权限模式不扩展已有任务授权。
- 负责：ACP v1 初始化、会话新建/加载、prompt、取消和 stdio 生命周期。
- 负责：把文本与 resource link 转为有界用户输入，把 `AgentEvent` 转为 ACP 文本、工具状态和终止原因。
- 负责：复用持久 foreground task service、Provider 配置和工具运行时；ACP Session ID 固定为 root conversation，Host checkpoint 显式选择当前实际 task/thread；终态续聊新 Task，恢复复用原冻结契约与累计预算，不复制 Agent loop。
- 负责：通过 ACP session mode 暴露 `auto` 与进程内 `session-all`；会话关闭、连接重建或进程退出后不继承临时授权，且不调用 ACP `request_permission`。
- 不负责：编辑器插件 UI、ACP v2 草案、客户端终端代理、未保存缓冲区同步、动态注入客户端 MCP server 或远程多用户服务。
- 首版必须与官方 Python SDK 的 ACP v1 类型和 stdio transport 对齐；对不支持的 content/capability 明确拒绝，不静默丢弃。

## Units
- `ChaosAcpAgent`: 实现 ACP v1 initialize/session/mode/prompt/cancel，生产由 Host task bridge 注入持久任务入口及选定历史 | 流式调用客户端 `session/update`，在 prompt 生命周期内应用会话权限 scope | 临时 scope 不扩大已冻结 Task 授权；串行 prompt，取消等待实际任务清理，queued prompt 取消不启动 Provider
- `acp_updates(event)`: 将模型文本、思考增量和工具生命周期事件映射为 ACP 更新 | 无副作用 | 不转发内部上下文和未受支持载荷
- `acp_stop_reason(event)`: 将 Chaos 终态映射为 ACP `stopReason` | 无副作用
