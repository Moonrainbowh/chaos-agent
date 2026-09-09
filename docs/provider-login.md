# 模型平台登录与配置

模型 profile 可选择 OAuth 账号登录或 API Key。登录凭据保存在工作区外，模型配置引用平台和认证方式。既有 API 配置保持有效；新平台配置追加为独立 profile，不覆盖已有 profile 或默认选择。

## 平台范围

认证流程参考 [URI Agent](https://github.com/4fuu/uri-agent/tree/cf8a52f1ee0334ccf9e169ceb81441f23f479895) 的 `src/oauth/mod.rs`，目录协议参考 `src/catalog.rs`。公开目录快照来自 [Pi 模型目录](https://pi.dev/api/models/providers)，采集日期为 2026-09-09。

| 平台标识 | 账号登录方式 | API Key |
|---|---|---|
| `antigravity` | Google 浏览器 OAuth，实验性私有协议 | 否 |
| `anthropic` | Claude Pro/Max 浏览器 OAuth | 是 |
| `workbuddy` | WorkBuddy 中国区浏览器登录 | 是 |
| `openrouter` | 浏览器 PKCE，生成用户 API Key | 是 |
| `openai-codex` | 浏览器或设备码 | 否 |
| `github-copilot` | GitHub 设备码，可指定企业域名 | 是 |
| `kimi-coding` | Kimi Code 订阅设备码 | 是 |
| `muse-code` | Meta 设备码 | 是 |
| `xai` | SuperGrok/X 订阅设备码 | 是 |
| `radius` | 浏览器或设备码 | 是 |

其他内置 API Key 平台：`abliteration`、`ant-ling`、`baseten`、`cerebras`、`cloudflare-ai-gateway`、`cloudflare-workers-ai`、`deepseek`、`fireworks`、`google`、`groq`、`huggingface`、`minimax`、`minimax-cn`、`moonshotai`、`moonshotai-cn`、`nvidia`、`openai`、`opencode`、`opencode-go`、`qwen-token-plan`、`qwen-token-plan-cn`、`qwen-token-plan-individual`、`together`、`vercel-ai-gateway`、`xiaomi`、`xiaomi-token-plan-ams`、`xiaomi-token-plan-cn`、`xiaomi-token-plan-sgp`、`zai`、`zai-coding-cn`。

Pi 快照列出 39 家，但 URI 当前可运行协议过滤不包含 `amazon-bedrock`、`azure-openai-responses`、`google-vertex`、`mistral` 对应协议。对齐后的内置平台注册表为 40 家：Pi 中兼容的 35 家加 URI 的 5 家扩展。此数量表示配置与协议范围，不表示全部平台都已用真实账号验收。

## 使用

Antigravity 需要在启动进程的环境中配置 `ANTIGRAVITY_OAUTH_CLIENT_ID` 和 `ANTIGRAVITY_OAUTH_CLIENT_SECRET`，使用有权使用的 OAuth 应用配置。仓库不再内置这两项值；缺失或空白时，登录和刷新会在打开浏览器或发送请求前停止，并提示缺失的变量名。已保存登录的刷新也需要相同应用配置；环境变量只从当前进程读取，不写入仓库。

在交互式 TUI 中输入 `/login` 并按 Enter，选择平台和登录方式。OAuth 按浏览器/设备码提示完成；API Key 在隐藏输入框中输入，Enter 确认、Esc 取消。不要把密钥直接写在命令后面。

登录后输入 `/logswitch` 选择已保存的登录及模型，也可以选择原有 API profile。方向键选择、键入过滤、Tab 补全、Enter 应用。直接输入示例：

```text
/login openai-codex browser
/login openai api_key
/logswitch openai-codex:oauth gpt-5.3-codex
/logswitch openai:api_key gpt-4.1
/logswitch 原有profile名称
```

`/logswitch` 只改变当前进程的选择，不覆盖配置文件默认值；下次启动仍按原默认配置。切换不同选择成功后开启空白新会话，旧记录保留；同值或失败保留当前会话。运行中的任务须先暂停。临时登录 profile 可在恢复旧任务时按保存的登录和模型目录重新构建，并继续校验原任务的模型身份。

不同平台的 OAuth 及 API Key 可以同时保留；同一平台每种认证方式目前保存一份凭据，再次登录该方式会替换它。重新登录当前使用的凭据会开启新会话；恢复旧记录时使用该凭据槽当前保存的账号，尚不保存多个同平台账号版本。Radius 等没有模型目录的平台仍需用下方 `auth configure` 明确模型和容量后重启加载 profile。

WorkBuddy 登录后，在 `/logswitch work` 中选择 `workbuddy:oauth` 加载入口并按 Enter，会请求当前账号的 `/v3/config` 并展开可运行模型；再次选择模型并按 Enter 才切换运行时。模型采用云配置中的实际 ID、输入/输出容量及 Chat Completions 协议；加载失败保留登录与当前模型，显示错误并允许重试。加载只在明确选择时联网，可用 Esc 取消；重启后仍使用已保存登录，无需重新授权。

以下命令在 PowerShell 中使用：

```console
chaos-agent auth providers
chaos-agent auth login openai-codex --method browser
chaos-agent auth login openai-codex --method device_code
chaos-agent auth login openai --api-key
chaos-agent auth login openai --api-key-env OPENAI_API_KEY
chaos-agent auth status
chaos-agent auth models openai
chaos-agent auth models --refresh
chaos-agent auth configure openai gpt-4.1 --profile openai-account
chaos-agent auth logout openai
```

`configure` 在已经登录的平台下生成 profile；可用 `--base-url`、`--api`、`--context-window`、`--max-output-tokens` 明确覆盖模型元数据。不同模型可能采用不同协议，同一平台默认地址不能替代该模型的实际地址。Cloudflare 平台还需要账号及网关标识。

OAuth 状态不含密钥明文；Windows 凭据使用当前用户 DPAPI 保护，默认保存在 `%LOCALAPPDATA%/chaos-agent/credentials.dat`。同一平台可以同时保存 OAuth 和 API Key，`configure --auth oauth` 或 `--auth api_key` 明确选择；未指定时优先已保存的 OAuth。`logout --auth oauth` 可只移除该方式，不指定则删除该平台两种本地凭据。认证失败显式返回，不自动切换到其他账号或 API Key。OpenRouter 账号授权最终签发 API Key，但保留 `oauth` 认证来源标识且不进行令牌刷新。

## 模型目录与验收边界

启动、导入模块和离线列出模型不联网。只有显式 `models --refresh` 获取公开 Pi 目录；单个平台刷新失败保留该平台旧目录。目录只提取模型标识、地址、协议、上下文及输出上限、文本/图像输入信息，不执行远程命令，不接受目录提供的认证 headers。

Radius 与 WorkBuddy 的模型可随账号权益变化，离线种子不编造模型标识，可用明确模型 ID 和必要参数配置。Antigravity 种子使用 URI 路由中的真实底层模型 ID，并保留实验性状态。目录列表不证明订阅权益或服务可用性。

本地验证使用 `httpx.MockTransport` 检查目录解析、协议筛选、部分失败保留、缓存重读、登录/刷新、协议请求及流式响应；另验证 Windows DPAPI、凭据共存、退出登录竞争及 CLI 配置到请求的整条路径。真实账号浏览器授权、订阅权益、平台端到端推理需要单独验收；本次不读取用户已有第三方私有凭据。Codex 当前使用 SSE；未对齐 URI 的 WebSocket 恢复或按账号实时模型发现。Antigravity 的多轮 thought signature 和 Claude reasoning 动态路由尚未验收，未知 reasoning 映射明确拒绝。

### 2026-09-09 验证记录

- WorkBuddy 空列表修复：从本机已保存 OAuth 登录只读请求云配置，实际返回 39 个工具调用聊天模型，并通过真实控制器注册 `login/workbuddy/oauth/auto`（Chat Completions，输入 168000、输出 32000）；未发送模型推理。相关 UI/集成 42 项、authentication 50 项、providers 80 项，共 172 项通过。

- `/login`、`/logswitch` 增量：Interfaces 定向 62 项、CLI/运行时集成 20 项、authentication 46 项、providers 79 项、config 30 项，共 237 项通过；覆盖隐藏输入、取消保存、双认证槽、默认配置不变、切回原 profile、历史任务运行时恢复及当前凭据重登后的新会话。
- TUI 增量 wheel 经 `pip wheel . --no-deps --no-build-isolation` 构建通过。真实浏览器授权、原生终端人工操作与平台推理尚未验收；额外既有 interaction 测试的 `test_running_icon_changes_but_completion_icon_is_static` 图标断言失败，未在登录功能中改动该图标逻辑。

- authentication 46 项、providers 79 项、config 30 项以及新增 CLI 集成 4 项通过。
- wheel 构建及独立目录安装检查通过，包内模型种子、认证命令和第三方许可证可读取；本仓库 `.venv` 直接加载更新后的源码。
- 全量回归未通过完整门禁：interfaces 停在 `StartupResponsivenessTests.test_escape_during_preparation_waits_for_cleanup_and_keeps_input`；独立根集成运行在 `FullStackTests.test_resume_never_replays_an_interrupted_command` 达到 180 秒上限。
- 其余 Feature 套件通过；workspace 为 457 项、其中跳过 20 项。没有修改上述启动/恢复测试相关实现，不能把本次结果写成全量发布验收通过。
