# Model Providers
把不同模型协议转换为统一、可流式处理的消息与工具调用接口。

## 边界
- 负责：OpenAI-compatible Responses、Chat Completions 与 Anthropic Messages 适配。
- 负责：流式事件规范化、超时与有限重试、能力声明、用量统计和配置校验。
- 不负责：Agent 回合决策、工具执行、上下文裁剪或界面渲染。
- 不负责：把 API key 写入会话、日志或项目文件。

