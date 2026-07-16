# Terminal UX、模型、Skills 与 MCP 完整化计划

## 状态

待用户确认后执行。本文件是下一轮实现的唯一计划来源，不改写历史
`plan.md`，也不替代既有发布验收文档。

## 目标

在保留 Windows Terminal 原生滚动、选择和复制能力的前提下，完成以下交付：

1. 修复 `/状态`、多行粘贴、双击 `Ctrl+C` 退出保护、工具过程缩进和 Markdown 三线表。
2. 用单一命令注册表统一斜杠命令的解析、帮助、调色板、补全、可用性和执行路由。
3. 闭合多模型 profile 的配置、切换、资源释放、任务审计和旧会话迁移生命周期。
4. 直接读取用户和工作区 `.agents/skills` 中的本地 Skills，并保持显式信任和上下文预算边界。
5. 使用成熟 MCP Python SDK 接通首批 `stdio` 服务，实现健康检查、工具发现、命名空间、调用、取消和 `ActionPolicy` 映射。
6. 最后在 `code_agent_win` 完成组合、CLI、配置、依赖和文档集成，并通过真实 Windows Terminal 验收。

## 已确认的产品决策

- API key 可以继续保存在工作区外的本地 `config.toml` 中；每个 profile 必须在 `api_key` 与 `api_key_env` 中二选一。
- 在迁移完成前，继续识别当前 `%LOCALAPPDATA%\code-agent\config.toml`；不得因创建 `%LOCALAPPDATA%\chaos-agent` 目录而让旧会话看起来消失。
- 模型切换只接受配置中已经存在并通过校验的 profile；斜杠命令不接收任意 URL、协议或 API key。
- 任务从创建到终态固定使用同一 profile；切换只改变下一任务的默认 profile，恢复任务使用其原始 profile。
- Skills 只读取用户和工作区的 `.agents/skills`；不新建或扫描 `.chaos-agent/skills`。
- Skill 首版不隐式执行脚本、不自动启用 MCP、不注册绕过策略的新工具；相关资源和脚本只能经现有工具与策略边界访问。
- MCP 首批以 `stdio` 为发布完成门槛；Streamable HTTP、OAuth、服务器安装和市场功能留到后续。
- 中间过程展示可审计的工具事实，不展示原始模型 reasoning。
- 表格默认采用无纵线的三线表；表格内部不混用青、灰、白三套结构色。
- `Ctrl+C` 第一次保留 TUI，第二次在两秒窗口内才退出。

## 非目标

- 不追求逐项复制 Codex 或 Claude Code 的所有命令。
- 不加入没有真实后端的 `/compact`、`/review`、`/plugin` 等占位命令。
- 不在 TUI 中编辑任意 provider URL、密钥、MCP 启动命令或权限策略。
- 不自动进行模型故障转移，不在活动任务中途更换 provider。
- 不让 Skill 或 MCP 绕过 `ActionPolicy`、工作区路径保护、审批和输出限制。
- 不把 Windows Terminal 变成全屏仪表板，也不接管字体、终端配色、选择或滚动历史。

## 全局工程约束

- 严格遵循“需求 -> 实现 -> 集成”顺序。
- 需求阶段只更新相关 Feature `AGENTS.md` 的目标和边界，不新增实现或 Units。
- 实现阶段只修改对应 `src/code_agent/<feature>/` 目录及其 Feature 测试。
- 集成阶段只修改 `code_agent_win/`、根配置、README 和根测试，不回头修改 Feature 源码。
- `windows_tui.py` 与 `terminal_state.py` 已接近 300 行上限；新增行为必须拆到小模块，不继续扩张两个聚合文件。
- 所有模型、工具、MCP 和配置错误必须脱敏；不得把 API key、认证头、完整配置文件或密钥后缀写入会话和日志。
- 所有终端输出继续经过控制字符清理；ANSI 只能由可信本地显示事件生成。
- 所有命令、输入、网络、子进程和输出使用明确上限与超时。

## 阶段 0：需求契约与基线

### 目标

先修正契约，使后续实现不与现有边界冲突，并记录当前缺陷的可重复基线。

### 允许修改

- `src/code_agent/interfaces/AGENTS.md`
- `src/code_agent/providers/AGENTS.md`
- `src/code_agent/config/AGENTS.md`
- `src/code_agent/skills/AGENTS.md`
- `src/code_agent/mcp/AGENTS.md`
- `src/code_agent/policy/AGENTS.md`（仅 MCP 风险映射边界确需补充时）
- `src/code_agent/sessions/AGENTS.md`（仅模型审计和会话迁移边界确需补充时）

### 契约增量

- Interfaces：粘贴块不自动提交、双击退出状态机、工具摘要白名单、三线表、单一命令目录和动态可用性。
- Providers：profile 固定任务、原子 client 切换、旧 client 关闭、恢复任务重建原 profile。
- Config：完整 profile 必填字段、`api_key`/`api_key_env` 二选一、旧配置迁移与脱敏表示。
- Skills：`.agents/skills` 发现、冲突隔离、显式激活、来源和 digest 审计、坏 Skill 隔离。
- MCP：SDK 生命周期、`stdio` 结构化启动、健康检查、工具 schema、命名空间和策略桥。
- Policy：MCP 工具不再统一落入 unknown；风险类别必须显式映射，未知仍拒绝。
- Sessions：任务记录保存 profile 身份；迁移可重复、保留旧库、失败不破坏源数据。

### 基线记录

- `/状态` 当前调用不存在的方法。
- `/帮助` 只显示有限候选，命令目录与解析器不一致。
- 粘贴 CR/CRLF 会触发多次提交或 steering。
- 单次 `Ctrl+C` 会退出，审批状态下还会同时拒绝并退出。
- 工具结果只显示 `<name> completed`，请求参数与有界结果摘要被丢弃。
- 表格使用完整方框，表头、边框和正文分别着色。
- profile 切换会泄漏后创建的 client，并可能显示错误模型。
- 新建 `chaos-agent` 目录可能让会话路径切换到空数据库。
- Skills 当前不读取现有 `.agents/skills`；MCP 只有 list/status。

### 退出门槛

- 每个 Feature 的目标和边界清楚，计划 Unit 数不超过 10。
- 不存在 UI 直接接收密钥、Skill 自动执行或 MCP 绕过策略的契约路径。
- 用户确认契约差异后才进入实现阶段。

## 阶段 1：终端输入和显示正确性

### 目标

优先解决误提交、误退出和明显显示问题，不改变 provider、Skills 或 MCP 行为。

### 预计实现方向

- 在 `interfaces` 内新增结构化输入事件，区分普通按键与粘贴块。
- Windows Terminal 优先启用 bracketed paste；不支持时使用有界控制台输入回退。
- 粘贴统一把 CRLF/CR 转成 `\n`，整块插入 `InputBuffer`，只重绘一次且绝不自动提交。
- 以 256 KiB 作为首版单次粘贴硬上限；超限时保留原输入并显示明确错误。
- 首行以 `/` 开头的多行粘贴在用户手工按 Enter 前不得执行命令。
- 新增可注入时钟的退出保护状态机：第一次处理当前上下文，第二次在两秒内退出，其他输入或超时解除 armed 状态。
- 运行中第一次 `Ctrl+C` 暂停任务并保存 checkpoint；审批中拒绝当前审批；有输入时清空输入；空闲时只显示退出提示。
- 真实 Windows 控制信号和 `\x03` 键事件归一到同一状态机，退出时恢复原控制处理器。
- 关联 `ActionRequest.id` 与 `ActionResult.request_id`，生成有界工具摘要，不再只保留工具名。
- 工具摘要默认缩进两格，详情缩进四格；连续工具记录不插入空行。
- 工具摘要白名单仅包含路径、项目数、行数、匹配数、改动数、返回码、验证类型、耗时和脱敏错误类型。
- Markdown 表格只画顶线、表头分隔线和底线，不画纵线；规则线暗灰，表头与正文中性白，状态符号保留语义色。
- 宽度不足时使用稳定的逐项布局，不输出截断或半截方框。

### 目标示例

```text
  ↳ list_files  src/code_agent/interfaces
    17 files · 84 ms
  ↳ read_file   terminal_renderer.py
    218 lines · 12 ms
```

```text
  ─────────────────────────────
  项目                 状态
  ─────────────────────────────
  代码结构             ✓ 完整
  实际训练运行         × 未执行
  ─────────────────────────────
```

### 自动化测试

- CRLF、LF、尾随换行、中文、Markdown 表格和 256 KiB 边界粘贴。
- 粘贴期间不调用 `submit()`，活动任务期间不产生逐行 steering。
- Ctrl+C 在 idle、输入非空、running、approval、超时和第二次退出状态下的行为。
- OS 控制信号适配器的安装、恢复和异常清理。
- 每种内置工具的摘要白名单、脱敏、缩进、失败和耗时显示。
- 三线表在 40、60、80、120 列宽下的 CJK 宽度、换行、颜色和 `NO_COLOR` 输出。
- 模型文本不能注入 ANSI 或控制序列。

### 退出门槛

- 一次多行粘贴只形成一个输入缓冲区内容和一次手工提交。
- 单次 Ctrl+C 在任何状态都不会直接关闭 TUI。
- 工具记录比当前行为更有信息，但不泄漏正文、命令、密钥或无界输出。
- 表格在目标宽度下没有纵线错位、交点变色或残缺边框。

## 阶段 2：单一命令注册表

### 目标

先稳定命令控制面，再让模型、Skills 和 MCP 使用同一发现与执行机制。

### 命令规范

每个命令由一个结构化定义声明：

- 稳定英文名与中英文别名。
- 分组、简短说明、用法和参数 schema。
- 可用性谓词和所需注入服务。
- 是否允许活动任务期间执行。
- 解析、补全和执行委托，不直接访问 provider 或工具。

注册表统一生成：

- 解析结果和结构化错误。
- `/帮助 [command]` 的分组内容。
- 输入区候选、Tab 补全和方向键选择。
- 当前 UI catalog 对应的显示标签。
- 动态隐藏不可用命令。

### 首批命令

- 通用：`/帮助`、`/状态`、`/清屏`、`/退出`、`/诊断`、`/追踪`。
- 会话：`/新建`、`/会话`、`/打开 <thread-id>`、`/恢复 <thread-id>`。
- 任务：`/任务`、`/任务 暂停|继续|停止|接受 [task-id]`、`/引导 <text>`。
- 工作区：`/差异 [path...]`、`/上下文`、`/工具`、`/证据 [task-id]`。
- 能力：`/模型 ...`、`/技能 ...`、`/mcp ...`。

任务恢复统一使用 `/任务 继续`，会话恢复使用 `/恢复`，不再共用含混的 `/resume` 语义。

### 自动化测试

- 注册表、解析、帮助、调色板和补全使用同一命令集合。
- 每个可见命令端到端可执行；没有服务的命令不可见。
- 中文和英文别名、参数错误、引号、空白和超长参数。
- Tab、上/下、Enter、Esc 与普通多行输入不冲突。
- `/状态`、未知 Skill、未知 MCP server 等错误留在 TUI 内，不退出进程。
- `/帮助` 展示完整分组而不是候选列表的前六项。

### 退出门槛

- 命令定义不存在重复清单。
- Palette 中不存在空壳命令，已实现命令也不会从帮助中消失。
- 所有成功和失败命令都通过本地化、脱敏的 in-band 结果反馈。

## 阶段 3：多模型生命周期与旧会话迁移

### 目标

让多个 URL、模型和密钥 profile 可真实切换、可审计、可恢复且不会泄漏资源或历史数据。

### 配置模型

每个 `[providers.<name>]` 必须显式声明：

- `api`
- `base_url`
- `model`
- `api_key` 或 `api_key_env`
- `context_window`
- `max_output_tokens`

Agent 预算字段继续支持显式覆盖。非空 profile 缺少协议、URL 或模型时必须失败，不得静默回退 OpenAI 默认值。

其他约束：

- 配置中的 profile 各自解析，不让全局 `CHAOS_*` 覆盖污染未选择的 profile。
- `base_url` 定义为服务根 URL；Responses、Chat Completions 和 Anthropic 路径单独解析，避免重复 `/v1`。
- 远程地址默认要求 HTTPS；localhost 可显式使用 HTTP。
- API key 不进入 repr、状态栏、异常、会话、trace、MCP 环境摘要或配置诊断。

### 运行时生命周期

- 新增 provider runtime manager，拥有当前 profile、client 和 runner 生命周期。
- 切换流程为“校验新 profile -> 构建新 client -> 原子替换 runner -> 更新状态 -> 关闭旧 client”。
- 任一步失败都保留旧 client 和旧 runner，不留下半切换状态。
- `--model` 形成可审计的临时 override，状态栏显示实际模型，并由同一 manager 关闭。
- profile 切换只改变下一任务默认值；活动、暂停、等待决策或验证中的任务均不得改变绑定 profile。
- 创建任务时保存 `profile_id`、模型、协议和脱敏 endpoint host；恢复时按记录重建原 profile。
- 缺失或已删除的历史 profile 进入明确的等待决策状态，不静默使用当前默认模型。

### 旧会话迁移

- 配置路径和 session DB 路径分别解析，不能以目录是否存在决定历史库位置。
- 若新库不存在而旧库存在，使用 SQLite backup API 原子复制到新库，不直接移动或删除旧库。
- 迁移前读取 schema/version 和 thread/task 数量；迁移后对比关键计数并执行完整性检查。
- 迁移使用临时文件和原子改名；失败时删除临时目标并继续保留旧库。
- 写入迁移 marker，重复启动保持幂等；旧库至少保留为只读备份，除非用户以后明确删除。
- 新旧库同时存在时不自动合并，必须给出明确诊断和人工选择，避免覆盖分叉历史。

### 模型命令

- `/模型` 或 `/模型 当前`
- `/模型 列表`
- `/模型 信息 <profile>`
- `/模型 使用 <profile>`
- `/模型 测试 [profile]`
- `/模型 重载`

列表和信息只显示 profile 名、模型、协议、endpoint host、上下文上限和密钥来源类型。

### 自动化测试

- 两个不同 URL、协议、模型和 key 来源的 profile 保持隔离。
- 缺少必填字段、重复 `/v1`、HTTP 远程地址和无效路径拒绝。
- 活动任务、暂停任务和恢复任务的 profile 固定行为。
- 成功切换、构建失败回滚、旧 client 关闭和应用退出关闭全部 client。
- `--model` override 的实际模型、状态栏和审计一致。
- 旧库迁移成功、重复迁移、损坏库、目标已存在和迁移中断。
- 配置、日志、异常和 session 序列化中不存在 API key。

### 退出门槛

- 至少两个真实 profile 可在空闲边界切换，下一请求使用目标 URL 和模型。
- 切换与退出后没有遗留 HTTP client。
- 旧线程和任务在新配置目录建立后仍可列出和恢复。
- 会话中的模型事实与实际发出请求的 profile 一致。

## 阶段 4：`.agents/skills` 读取与激活

### 目标

直接复用 `.agents/skills` 中已有 Skills，同时保持来源、信任、上下文预算和执行权限透明。

### 发现根与格式

只扫描以下两个同格式根：

- 用户级：`%USERPROFILE%\.agents\skills`
- 工作区级：`<workspace>\.agents\skills`

不创建或扫描 `.chaos-agent/skills`、`.codex/skills`、`.claude/skills`。

支持：

- 每个 Skill 目录直接读取 `SKILL.md`，从受支持 frontmatter 读取名称和描述；没有 frontmatter 时以目录名作为候选 ID。
- Skill 引用的 `references/`、`scripts/`、`assets/` 和模板仅作为受限资源目录；发现阶段不执行其中内容。

### 冲突和信任

- 相同 ID、相同 digest 的重复安装合并展示来源。
- 相同 ID、不同 digest 标为冲突并禁用，直到用户明确选择来源。
- 单个无效 Skill 进入诊断列表，不阻止其他 Skill 或应用启动。
- 用户目录 Skill 与工作区 Skill分别显示来源和信任级别；工作区 Skill 默认需要显式激活。
- 记录活动 Skill 的 ID、来源和 digest，不把完整内容复制进会话事实。
- 上下文构建继续执行字符/token 预算，超限给出可修复错误。

### Skills 命令

- `/技能 列表 [--all|--active|--errors]`
- `/技能 信息 <id>`
- `/技能 启用 <id>`
- `/技能 禁用 <id>`
- `/技能 来源 <id>`
- `/技能 重载`

显式调用和持久化激活只在真实后端完成后出现在 palette；首版至少保证会话级激活和任务事实持久化。

### 自动化测试

- 用户级和工作区级 `.agents/skills`、frontmatter、Unicode 和有界文件读取。
- 相同 digest 去重、不同 digest 冲突、无效目录隔离和符号链接拒绝。
- 工作区 Skill 显式激活、会话恢复、digest 改变后的重新确认。
- 上下文预算、顺序稳定性和无工具/权限扩张。
- Skill 中的指令不能自动启用 MCP、执行脚本或修改配置。

### 退出门槛

- 至少两个现有 `.agents/skills` Skill 可被发现、查看、启用、禁用和重载。
- 坏 Skill 不影响应用启动。
- 活动 Skill 的来源、digest 和上下文成本可查询。
- Skill 不能新增未经策略批准的能力。

## 阶段 5：MCP SDK、健康检查和策略桥

### 目标

将预配置的 `stdio` MCP server 接入统一工具系统，不让服务连接静默扩大 Agent 权限。

### SDK 与配置

- 使用成熟的 MCP Python SDK，不自行实现 JSON-RPC、握手或协议状态机。
- 在 Feature 内通过可注入 adapter 隔离 SDK，单元测试使用内存 fake；实际 SDK 依赖在集成阶段声明。
- `stdio` 配置使用结构化 `command`、`args`、工作目录和环境变量引用，不接受拼接后的 shell 命令字符串。
- 环境变量只允许显式白名单传递；状态和错误不显示 secret 值。
- 只有 `enabled=true` 且已经批准的 server 才能启动或暴露 namespace。

### 生命周期

- 懒启动或显式启用 server。
- SDK initialize/handshake、`tools/list`、schema 校验和健康状态。
- 有界启动、调用和关闭超时。
- `stderr` 有界捕获与脱敏；stdout 保留给协议，不混入终端日志。
- server 崩溃后标记 unhealthy；自动重连有次数上限，支持显式 restart。
- TUI 退出时先取消 in-flight call，再关闭 session 和子进程。

### 工具命名空间与策略

- MCP 工具统一命名为 `mcp.<server>.<tool>`。
- 拒绝 namespace 冲突、非法 schema、重复工具和动态改变风险映射的 server。
- 每个 server/tool 必须映射 `read`、`write`、`network` 或 `critical` 风险；未映射工具保持拒绝。
- MCP 调用转换为 typed action request，经过与内置工具相同的 `ActionPolicy`、审批、取消、超时、结果限制和审计流程。
- server 自报的只读属性只能作为提示，不能替代本地风险配置。
- Skill 可以声明需要某个 MCP server，但不能自动启用或批准它。

### MCP 命令

- `/mcp 列表`
- `/mcp 状态 [server]`
- `/mcp 工具 <server>`
- `/mcp 启用 <server>`
- `/mcp 禁用 <server>`
- `/mcp 重启 <server>`
- `/mcp 诊断 <server>`

### 自动化测试

- initialize、健康检查、tools/list、tool call、取消、超时和正常关闭。
- server 不存在、启动失败、崩溃、重连上限、stderr 过量和 schema 非法。
- disabled/unapproved server 不启动、不生成 namespace。
- namespace 冲突、未知 risk、read/write/network/critical 策略路径。
- 审批拒绝时不向 server 发出调用。
- 输出过大、控制字符、恶意 tool 名和 secret 泄漏防护。
- 使用 SDK 构建的本地只读测试 server 完成一次真实 stdio 握手和调用。

### 退出门槛

- 至少一个真实 stdio server 可被启动、检查、列出工具、调用和干净关闭。
- 每次 MCP 调用都有本地风险类别、策略决定和会话审计。
- unknown、disabled 或未批准工具不能被模型看到或调用。
- server 故障不会让 TUI 崩溃，也不会遗留子进程。

## 阶段 6：应用集成

### 目标

只在所有 Feature API 和单元测试稳定后修改 Windows 应用组合层。

### 允许修改

- `code_agent_win/app.py`
- `code_agent_win/cli.py`
- `code_agent_win/tools.py`
- `code_agent_win/tool_support.py`（如 MCP typed result 需要）
- `pyproject.toml`
- `README.md`
- `tests/`
- 发布与验收文档

### 集成内容

- 组合结构化输入、退出保护、工具摘要、三线表和命令注册表。
- 注入 provider runtime manager，统一 TUI、CLI、前台任务和应用关闭生命周期。
- 执行旧配置/session 迁移并保留兼容别名。
- 组合用户级和工作区级 `.agents/skills` registry、会话激活和上下文构建器。
- 声明兼容 Python 3.10 的 MCP SDK 依赖，组合 stdio manager 与 policy bridge。
- 让内置工具和 MCP 工具通过同一个动态 tool catalog 暴露给模型。
- CLI 参数改为顺序无关，并补充 `--help`、`doctor`、profile、Skills 和 MCP 的非交互查询入口。
- README 记录实际支持的配置 schema、迁移、命令、`.agents/skills` 发现范围、MCP 信任模型和退出语义。
- 不修改 Feature 内部实现；若接口有问题，返回对应实现阶段处理并重新验证。

### 自动化验证

```powershell
python -m unittest discover -s src\code_agent\interfaces\tests -p 'test_*.py' -v
python -m unittest discover -s src\code_agent\providers\tests -p 'test_*.py' -v
python -m unittest discover -s src\code_agent\config\tests -p 'test_*.py' -v
python -m unittest discover -s src\code_agent\skills\tests -p 'test_*.py' -v
python -m unittest discover -s src\code_agent\mcp\tests -p 'test_*.py' -v
python -m unittest discover -s src\code_agent\policy\tests -p 'test_*.py' -v
python -m unittest discover -s src\code_agent\sessions\tests -p 'test_*.py' -v
python -m unittest discover -s tests -p 'test_*.py' -v
python -m build
git diff --check
```

### 真实集成烟测

- 使用两个真实 provider profile 分别执行最小 `ask`，核对 endpoint、模型、状态栏和任务审计，不打印 key。
- 切换 profile 后退出，确认所有 client 关闭；恢复旧任务时使用原 profile。
- 从旧会话库迁移后列出并恢复历史线程，核对关键计数。
- 从 `.agents/skills` 发现并激活两个现有 Skill，确认上下文和 digest 记录。
- 启动一个 SDK stdio MCP server，执行 health、tools/list 和一个只读工具调用，确认审批与审计。

### 退出门槛

- Feature、根集成和打包验证全部通过。
- 可编辑安装后的 `chaos-agent` 和兼容 `agent` 入口可启动。
- 文档只描述已接通能力，不保留 list/status-only 等过时声明。

## 阶段 7：真实 Windows Terminal 验收

### 环境

- Windows Terminal，默认字体与用户现有 profile。
- 颜色 `auto` 与 `never` 各一次。
- 宽窗口与约 50 列窄窗口各一次。
- 使用真实本地配置、旧会话库副本、两个 provider profile、现有 Skills 和一个 stdio MCP server。

### 验收清单

1. 粘贴包含中文、空行和 Markdown 表格的 20 行文本，确认输入区只出现一个粘贴块，不自动提交。
2. 手工 Enter 后只出现一条用户消息，不产生逐行 steering。
3. 分别在 idle、输入非空、任务运行和审批中按一次 Ctrl+C，确认 TUI 保留；两秒内第二次才退出。
4. 第二次退出后重新启动并恢复 checkpoint，确认任务和会话未损坏。
5. 触发连续 `list_files`、`read_file`、编辑和验证操作，确认缩进、目标、规模、耗时和错误摘要清楚且不顶格。
6. 渲染长中文、多列和窄窗口表格，确认三线表没有纵线错位、交点变色或残缺边框。
7. 输入 `/`，使用过滤、Tab、方向键、帮助和错误恢复；确认 `/状态` 不崩溃且帮助完整。
8. 切换两个模型 profile，确认状态栏和下一任务使用目标模型；活动任务期间切换被拒绝。
9. 迁移并打开旧线程，确认历史、任务和 checkpoint 可见。
10. 从 `.agents/skills` 发现、查看、启用、禁用和重载 Skills；坏 Skill 只显示诊断。
11. 启用 MCP server、检查健康和工具列表、调用只读工具、拒绝高风险工具并重启 server。
12. 关闭 TUI 后确认无遗留 provider 连接、MCP 子进程或未恢复的终端模式。

### 验收证据

- 保存不含密钥的命令输出和关键截图。
- 记录 Windows Terminal 版本、列宽、颜色模式和 profile 名称。
- 对每一项标记通过、失败或阻塞；不得以自动化测试替代未执行的真实终端步骤。

## 评审门

1. 用户评审阶段 0 契约后进入实现。
2. 阶段 1 完成后先评审一次真实粘贴、Ctrl+C 和三线表截图。
3. 阶段 3 完成后评审 profile 配置、任务固定和迁移证据。
4. 阶段 4 完成后评审 Skill 发现、冲突和信任行为。
5. 阶段 5 连接真实 server 前评审 MCP 配置 schema 与风险映射。
6. 所有 Feature 通过后才允许修改 `code_agent_win` 集成层。
7. Windows Terminal 人工验收全部有证据后才宣称计划完成。

## 建议提交拆分

1. `docs: 明确终端与能力完整化契约`
2. `fix: 修复终端粘贴退出与显示行为`
3. `refactor: 统一斜杠命令注册表`
4. `feat: 闭合多模型与旧会话迁移生命周期`
5. `feat: 接入 .agents Skills 发现与激活`
6. `feat: 接入 MCP SDK 与策略桥`
7. `feat: 集成终端能力并补全验收`

每个提交前运行对应 Feature 测试；最终提交前运行完整矩阵、构建和真实终端验收。

## 风险与回退

- 会话迁移：始终保留旧 DB，临时目标失败即删除，不自动合并两个已有库。
- provider 切换：新 client 未通过构建/烟测时不替换旧 runner；替换失败原子回退。
- 终端输入：退出时无条件恢复控制模式；bracketed paste 不可用时退回有界输入事件聚合。
- Skills：冲突或解析失败只禁用对应条目，不扩大为应用启动失败。
- MCP：启动、握手、schema 或风险映射失败时不暴露工具；关闭失败强制终止受管子进程并记录脱敏诊断。
- SDK 兼容：先在 Python 3.10 和现有 `httpx` 依赖上验证，再锁定兼容版本范围。

## 完成定义

只有同时满足以下条件，计划才算完成：

- 用户列出的五组工作全部实现，不存在只写文档或只显示状态的占位能力。
- 多行粘贴、双击 Ctrl+C、工具详情缩进和三线表通过自动化及真实 Windows Terminal 验收。
- 命令注册表是解析、帮助、palette、补全和执行可用性的唯一来源。
- 多模型切换无 client 泄漏，任务 profile 可审计，旧会话无丢失观感。
- `.agents/skills` 中的 Skills 可真实发现和激活，冲突、错误和信任边界可见。
- MCP stdio 服务可真实连接、检查、调用、取消和关闭，且每个工具经过 `ActionPolicy`。
- Feature、根测试、构建、`git diff --check` 和人工验收全部通过。
- 验收文档如实区分已通过、失败和外部阻塞，不把局部测试通过表述为完整交付。
