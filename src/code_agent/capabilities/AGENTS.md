# Progressive Tool Capabilities
以紧凑能力目录取代每回合的全量工具 schema，并在模型显式读取契约后才暴露目标工具。

## 边界
- 负责：从当前 `ToolDefinition` 快照生成有界的能力目录、契约读取工具和本次 Agent run 已展开工具投影。
- 负责：契约读取返回完整名称、说明和 JSON Schema；目标工具只从下一模型回合起可调用。
- 负责：MCP、Plugin 或模式投影变化时每回合使用最新快照；已撤下的工具不得因旧展开状态继续出现。
- 不负责：执行工具、授权、审批、风险决策、Provider 协议转换或跨次用户请求持久化展开状态。
- 契约目录是发现面，不是权限面；实际调用仍产生强类型 `ActionRequest` 并走现有 dispatcher。

## Units
- `progressive_tools(tools, disclosed_names)`: 生成当前目录 loader 与本次 run 已展开 schema 投影 | 无副作用 | loader 不存在时保持 legacy 全量工具行为，已撤下名称自动消失
- `contract_result(request, tools)`: 从当前快照返回一个完整 `ToolDefinition` 和下回合可用标记 | 无副作用 | 未知名称或多余参数返回结构化错误
- `disclosed_name(result)`: 仅从成功的契约读取结果取出已展开工具名 | 无副作用 | 不信任失败输出
