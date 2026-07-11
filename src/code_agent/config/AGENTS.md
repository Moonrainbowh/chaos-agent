# Local API Configuration
为个人 Windows 用户加载并验证工作区外的本地 Provider 配置，同时建立明文密钥的内部边界。

## 边界
- 负责：解析默认或显式配置路径、读取 TOML、验证 provider profile、合并既有环境变量和 CLI profile 覆盖，以及生成可安全展示的运行配置。
- 负责：保存配置内明文 `api_key` 的私有表示，确保其不会出现在公开配置、诊断、序列化或异常内容中。
- 不负责：发送网络请求、持久化会话、执行工具或渲染终端界面。
- 不负责：提供操作系统级密钥隔离；同一 Windows 用户经明确批准运行的进程仍可读取其本地配置文件。
- 依赖：Python 3.10+ 的 TOML 兼容解析器；默认配置目录为 `%LOCALAPPDATA%\\code-agent`，不属于工作区。

## Units
- `default_config_path(env)`、`resolve_config_path(env)`: 解析默认或绝对覆盖配置文件路径 | 无副作用 | 相对 `CODE_AGENT_CONFIG` 拒绝
- `load_runtime_config(env, cli_profile)`: 读取、验证、选择并合并本地 provider 配置 | 文件 I/O | 缺失文件回退既有环境变量；异常不包含文件内容或密钥
- `RuntimeConfig`: 冻结已选择的 Provider、profile、审批模式、敏感路径开关和配置路径 | 无副作用 | 密钥状态只能显示脱敏描述
