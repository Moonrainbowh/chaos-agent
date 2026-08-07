# Model Providers
把不同模型协议转换为统一、可流式处理的消息与工具调用接口。

## 边界
- 负责：OpenAI-compatible Responses、Chat Completions 与 Anthropic Messages 适配。
- 负责：流式事件规范化、超时与有限重试、能力声明、用量统计和配置校验。
- 负责：按 profile 显式声明的输入模态，在发起网络请求前解析并校验附件引用；把图片和文本附件分别映射为 Responses、Chat Completions 与 Anthropic Messages 的原生 content blocks。
- 负责：缺失能力、缺失/损坏 blob、不允许的 MIME 或超限附件必须在零网络请求下失败闭合；纯文本消息保持既有字符串 payload。
- 负责：按名称解析模型 profile；profile 声明模型上下文、输出、协议、认证引用及 Agent 预算。
- 负责：向上层提供只读的已配置 profile 目录与当前 profile 信息；只在空闲或下一任务边界构造不可变 provider 配置和 client。
- 负责：管理 profile、client 和 runner 的原子生命周期；切换先构建并校验新 client，再替换并关闭旧 client。
- 负责：固定任务的 profile 身份；恢复任务按原 profile 重建，缺失 profile 明确进入等待决策。
- 不负责：Agent 回合决策、工具执行、上下文裁剪或界面渲染。
- 不负责：把 API key 写入会话、日志或项目文件。
- 不负责：从模型名猜测视觉能力、读取原附件路径、持久化附件，或把引用元数据原样发送给 Provider。
- 不负责：接受 UI 提供的任意 URL、协议或 API key；不在活动流或任务中途重建 provider。
- 依赖：运行时使用 `httpx`；测试只能使用 `MockTransport` 或自定义内存字节流。
- 密钥边界：配置保存环境变量名或私有、本地配置密钥来源；请求时才读取密钥，公开表示、序列化和异常不得包含认证头或明文密钥。
- 资源边界：事件、完整响应、单个工具参数和工具调用总数分别受独立正数配置限制。

## Units
- `ProviderError` 及子类：表达配置、HTTP、协议和响应上限失败 | 无副作用 | 对外消息执行脱敏
- `ProviderConfig`、`ApiProtocol`: 校验并冻结端点、协议和传输限制 | 请求时读取 API key 环境变量
- `InputModality`、`ModelProfile`、`ModelProfileResolver`: 显式校验单模型输入模态、提供方和 Agent 限制，并按 CLI 模型名选择 profile | 请求时读取 API key 环境变量 | 缺失模态声明默认仅 text
- `AttachmentResolver`、`ProviderAttachmentEncoder`: 逐引用复核 blob 并生成三种协议的图片/不可信文本原生块 | 调用注入 resolver | 能力、缺失和完整性失败发生在网络请求前
- `ProviderRuntimeManager`: 原子切换、关闭和审计当前 profile/client/runner | 网络资源生命周期 | 失败保留旧运行时，活动任务不得切换
- `SSEDecoder.feed(chunk)`: 有界增量解码 UTF-8 SSE 事件 | 保存未完成行与事件状态
- `ArgumentBuffer`、`ToolBudget`: 按 UTF-8 字节累计工具参数并限制工具调用数 | 保存当前流的有界分片
- `ProviderTransport.stream_sse(path, payload)`: 禁止重定向，限制响应总量并按白名单有限重试 | 网络 I/O；仅关闭内部创建的 client
- `OpenAIChatClient.stream(system_prompt, messages, tools)`: 适配 Chat Completions 文本、推理、工具与用量流，并将内部 developer checkpoint 合并到首个 system 消息 | 网络 I/O | 不向仅兼容传统 Chat 角色的服务发送 developer 角色
- `OpenAIResponsesClient.stream(system_prompt, messages, tools)`: 适配 Responses item/call 事件并去重工具调用 | 网络 I/O
- `AnthropicClient.stream(system_prompt, messages, tools)`: 适配 Messages content block、工具输入与累计用量 | 网络 I/O
