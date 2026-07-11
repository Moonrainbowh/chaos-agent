# Model Providers
把不同模型协议转换为统一、可流式处理的消息与工具调用接口。

## 边界
- 负责：OpenAI-compatible Responses、Chat Completions 与 Anthropic Messages 适配。
- 负责：流式事件规范化、超时与有限重试、能力声明、用量统计和配置校验。
- 不负责：Agent 回合决策、工具执行、上下文裁剪或界面渲染。
- 不负责：把 API key 写入会话、日志或项目文件。
- 依赖：运行时使用 `httpx`；测试只能使用 `MockTransport` 或自定义内存字节流。
- 密钥边界：配置只保存环境变量名，请求时才读取密钥；异常不得包含认证头或完整响应体。
- 资源边界：事件、完整响应、单个工具参数和工具调用总数分别受独立正数配置限制。

## Units
- `ProviderError` 及子类：表达配置、HTTP、协议和响应上限失败 | 无副作用 | 对外消息执行脱敏
- `ProviderConfig`、`ApiProtocol`: 校验并冻结端点、协议和传输限制 | 请求时读取 API key 环境变量
- `SSEDecoder.feed(chunk)`: 有界增量解码 UTF-8 SSE 事件 | 保存未完成行与事件状态
- `ArgumentBuffer`、`ToolBudget`: 按 UTF-8 字节累计工具参数并限制工具调用数 | 保存当前流的有界分片
- `ProviderTransport.stream_sse(path, payload)`: 禁止重定向，限制响应总量并按白名单有限重试 | 网络 I/O；仅关闭内部创建的 client
- `OpenAIChatClient.stream(system_prompt, messages, tools)`: 适配 Chat Completions 文本、推理、工具与用量流 | 网络 I/O
- `OpenAIResponsesClient.stream(system_prompt, messages, tools)`: 适配 Responses item/call 事件并去重工具调用 | 网络 I/O
- `AnthropicClient.stream(system_prompt, messages, tools)`: 适配 Messages content block、工具输入与累计用量 | 网络 I/O
