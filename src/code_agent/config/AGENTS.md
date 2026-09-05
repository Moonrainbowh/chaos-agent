# Local API Configuration
为个人 Windows 用户加载并验证工作区外的本地 Provider 配置，同时建立明文密钥的内部边界。

## 边界
- 支持本地可信 profile 的 `model_metadata.token_budget` 默认启用 persistent；显式 `context_policy`（包括 false）优先，不联网猜测能力、不接受模型输出作为配置。
- 负责：解析默认或显式配置路径、读取 TOML、验证 provider profile、合并既有环境变量和 CLI profile 覆盖，以及生成可安全展示的运行配置。
- 负责：校验每个 profile 的 `api`、`base_url`、`model`、`context_window`、`max_output_tokens` 和恰好一种密钥来源，并单独解析各 profile 以避免全局环境污染。
- 负责：校验每个 profile 的显式 `input_modalities`；缺失时默认仅 `text`，只接受受支持、去重的 `text`/`image` 组合。
- 负责：分别解析配置与会话路径，兼容旧 `code-agent` 配置；不以新目录是否存在决定旧会话是否可见。
- 负责：解析 `legacy` / `hybrid` / `progressive` 工具能力策略，缺失时采用推荐的 `hybrid`。
- 负责：为上层提供只包含已配置 profile 标识、模型和非敏感能力摘要的只读目录；保留 profile 选择所需的私有配置，供安全任务边界重新构造运行时使用。
- 负责：保存配置内明文 `api_key` 的私有表示，确保其不会出现在公开配置、诊断、序列化或异常内容中。
- 不负责：发送网络请求、持久化会话、执行工具或渲染终端界面。
- 不负责：提供操作系统级密钥隔离；同一 Windows 用户经明确批准运行的进程仍可读取其本地配置文件。
- 不负责：持久化未验证的 UI 配置、暴露 API key 或接受任意 provider 端点、协议和认证信息。
- 不负责：按模型名称推断多模态能力，或在配置中保存附件路径、blob 和 base64。
- 依赖：Python 3.10+ 的 TOML 兼容解析器；默认配置目录为 `%LOCALAPPDATA%\\chaos-agent`，不属于工作区；读取旧 `%LOCALAPPDATA%\\code-agent` 配置和 `CODE_AGENT_*` 环境变量作为兼容迁移路径。

### 预算框架（显式启用的 v1 已实现）

- provider 可显式配置 `context_policy`：工作窗、准备/换窗比例、安全余量、交接大小及独立任务额度。缺少显式策略与启用 metadata 时为旧摘要路径；显式策略未知字段拒绝，私人配置不自动修改。
- 2026-09-05 的配置、验证与实验边界见根目录 `docs/context-boundary-experiment.md` 和 `docs/context-boundary-results.md`；具体候选值可配置，实验结果不自动推广为默认策略。

## Units
- `default_config_path(env)`、`resolve_config_path(env)`: 解析默认或绝对覆盖配置文件路径 | 无副作用 | 相对 `CHAOS_CONFIG` 拒绝；无新配置时回退旧目录
- `load_runtime_config(env, cli_profile)`: 读取、验证、选择并合并本地 provider 配置 | 文件 I/O | `CHAOS_*` 优先、`CODE_AGENT_*` 回退；异常不包含文件内容或密钥
- `RuntimeConfig`: 冻结已选择的 Provider、profile、审批模式、敏感路径开关、PowerShell 方言请求、能力策略和配置路径 | 无副作用 | 能力策略默认 hybrid 且只接受 legacy/hybrid/progressive；方言缺失为兼容期 `auto`；默认审批模式为 `auto`；密钥状态只能显示脱敏描述
- `configured_capability_strategy(document, env)`: 合并 `[agent].capability_strategy` 与新旧环境变量 | 无副作用 | `CHAOS_CAPABILITY_STRATEGY` 优先，未知值失败闭合
- `ProfileSummary`: 提供 profile 名、模型、协议、endpoint host、预算和密钥来源类型 | 无副作用 | 不包含密钥、URL 路径或认证头
- `_input_modalities(values)`: 解析 profile 显式 `text`/`image` 输入能力 | 无副作用 | 默认仅 text，重复、未知和缺少 text 均失败闭合
- `configured_context_policy`: 解析显式 context_policy 或可信本地 model_metadata.token_budget 默认值 | 无副作用 | 显式 false 禁用且优先；缺少 enabled 不启用；任务预算与工作窗独立
