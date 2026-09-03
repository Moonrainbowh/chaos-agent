# Progressive Tool Capabilities
以可切换的全量、混合或渐进策略投影 provider 工具定义，并用短确认加载长尾契约。

## 边界
- 负责：从当前 `ToolDefinition` 快照生成 `legacy`、`hybrid`、`progressive` 三种本次 Agent run 工具投影；默认 `hybrid`。
- 负责：`hybrid` 只按精确白名单预热内置高频只读工具；MCP、Plugin 和其他长尾工具保持渐进加载。
- 负责：契约读取只返回 `name`、完整定义的稳定 `digest` 和 `availability`；完整 L2 schema 只在下一模型回合的 provider 工具定义中出现。
- 负责：披露状态只保存 `tool name -> schema digest`；MCP、Plugin 或模式投影变化时每回合使用最新快照，已撤下或 digest 变化的工具自动恢复为未披露。
- 不负责：完整 Capability Manifest、generation/lease、执行工具、授权、审批、风险决策、Provider 协议转换或跨次用户请求持久化展开状态。
- 契约目录是发现面，不是权限面；实际调用仍产生强类型 `ActionRequest` 并走现有 dispatcher。

## Units
- `CapabilityStrategy`: 表达 `legacy`、`hybrid`、`progressive` 三种 A/B 策略 | 无副作用 | 默认由上层显式选择 `hybrid`
- `progressive_tools(tools, disclosed_tools, strategy)`: 以当前定义重新校验 name/digest 后生成 provider 工具投影 | 无副作用 | legacy 去除 loader 后全量暴露；已撤下或 schema digest 变化的披露自动失效
- `contract_result(request, tools)`: 从当前快照返回 `name + digest + availability` 短确认 | 无副作用 | 不把说明或 schema 写入工具结果；未知名称或多余参数返回结构化错误
- `tool_definition_digest(definition)`: 对完整当前 `ToolDefinition` 做规范 JSON SHA-256 摘要 | 无副作用 | digest 变化表示 provider 定义变化，不替代完整定义
- `disclosed_contract(result)`: 从成功契约结果提取并校验 `name + digest` | 无副作用 | 名称、availability 或 SHA-256 digest 不合法时不更新披露状态
