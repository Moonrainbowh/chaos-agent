# 编程 Agent CLI/TUI 设计研究与 Chaos4 落地建议

> - 研究对象：Amp、Codex CLI、Claude Code、uv-agent、DeepSeek-TUI / CodeWhale
> - 项目对象：Chaos Agent（仓库 <code>G:\code-test\chaos4</code>）
> - 研究快照：2026-07-16（Asia/Shanghai）
> - 交付类型：设计研究报告，不包含源码实现

## 阅读导航

- 先看结论：第 0、5、6 节。
- 看各产品设计：第 4 节。
- 看 Chaos4 目标 UI 与交互：第 7–15 节。
- 看工程架构、阶段与验收：第 16–19 节。
- 看证据边界与来源：第 20–23 节。

## 0. 执行摘要

这五个上游产品并不存在一个可以原样复制的“最佳 TUI”。它们分别把重点放在不同位置：

- Amp 最强的是命令面板、Thread 工作记录、queue / steer / interrupt 三档干预和多 Agent 远程运行；最大风险是工具默认不询问批准。
- Codex CLI 最强的是把模型能力、OS 沙箱、审批策略、工作区 diff 和会话控制拆成可审计的独立层。
- Claude Code 最强的是长任务交互闭环：Transcript Viewer、后台任务、任务清单、逐提示 checkpoint、diff 与 rewind。
- uv-agent 最有价值的是“一个模型出口 + 可审计脚本 + 渐进式上下文 + ANSI 单列 TUI”的实验；但单一 <code>run_python</code> 边界不等于安全沙箱，且同一发布中的文档、CLI 和按键契约存在漂移。
- DeepSeek-TUI 已正式改名为 CodeWhale。它最值得研究的是 Plan / Agent / YOLO 与审批姿态分离、side-git 按轮回滚、Provider 路由和并发子 Agent；其功能面也最容易膨胀成复杂控制台。

对 Chaos4 的建议不是改成全屏 Dashboard，而是继续坚持当前已经形成的产品主线：

1. 保留“正常终端滚屏 + 单列 Transcript + 底部 Composer + 最小动态尾部”。
2. 把 <strong>任务模式、实际模型、权限姿态、任务状态</strong> 继续作为四个互不替代的状态。
3. P0 已接入真实 <code>thread_id</code> 驱动的语义 checkpoint、可追溯 Source Anchor、完整 mutation capture 与任意 checkpoint 的只读 rewind preview；破坏性 apply / restore 仍明确不提供。
4. 在现有 <code>CommandRegistry</code> 上统一 <code>/</code>、快捷键帮助、命令可用性、插件贡献和中英文别名；新增 <code>@</code> 文件与 <code>@@</code> Thread 选择。
5. 子 Agent 继续保持“结果只作建议、不能代替验证”；增加可观察、可取消、可 steer 的运行视图，并保持单写者租约。
6. 不采用 Amp 的默认无审批，不把 uv-agent 的任意 Python 作为模型唯一执行边界，不让插件裸执行宿主代码。
7. daemon、远程控制、自动 worktree、commit、push 暂不进入近期主线；若未来需要，应作为独立产品决策，而不是 TUI 美化的附带功能。

## 1. 范围、术语与身份核验

### 1.1 CLI 与 TUI 的边界

- CLI：进程入口、参数语法、非交互运行、退出码、stdout / stderr、JSONL 协议、会话恢复和自动化集成。
- TUI：终端内的交互投影，包括 Transcript、Composer、Picker、状态、审批、diff、任务和子 Agent 视图。
- Runtime：会话、策略、工具、任务、上下文和审计的真实状态机。TUI 应只是 Runtime 的投影，不能自己发明“已应用”“已验证”或“已完成”。

### 1.2 项目名称核验

| 用户称呼 | 核验后的正式对象 | 结论 |
| --- | --- | --- |
| Amp | Sourcegraph / Amp，官网为 ampcode.com | 纳入 |
| Codex | OpenAI Codex CLI，官方仓库 openai/codex | 纳入，只讨论 CLI/TUI |
| ClaudeCode | Anthropic Claude Code | 纳入，只讨论 CLI/TUI |
| uv-agent | uv-agent/uv-agent | 纳入，并作为 Chaos4 的 clean-room 上游参考 |
| DeepSeekTUI、Whale Code | Hmbown/DeepSeek-TUI 已改名 Hmbown/CodeWhale | 正式名称是 <strong>CodeWhale</strong> |
| usewhale/Whale | 独立的 Go 项目，拥有不同 owner 和仓库身份 | 排除，不能当作 DeepSeek-TUI 的改名目标 |

DeepSeek-TUI → CodeWhale 的同一性由旧仓库重定向、固定 GitHub repository ID、v0.8.41 发布说明和官方 <code>REBRAND.md</code> 共同支持；命令映射是 <code>deepseek → codewhale</code>、<code>deepseek-tui → codewhale-tui</code>。[CodeWhale 政名文档][CW-REBRAND] [v0.8.41 发布说明][CW-RELEASE]

CodeWhale 是独立社区项目，不是 DeepSeek 官方产品。另一个 <code>usewhale/Whale</code> 是不同仓库、不同语言和不同迁移链。[CodeWhale README][CW-README] [usewhale/Whale][WHALE-OTHER]

## 2. 研究方法与证据等级

本报告采用三路子 Agent 并行调查，再由主 Agent 复核：

- Agent A：Amp 官方手册、新闻、CLI/TUI、权限与插件。
- Agent B：Codex CLI 与 Claude Code 官方文档，严格排除桌面端和 IDE 特有能力。
- Agent C：uv-agent 发布提交、源码 / 文档漂移，以及 DeepSeek-TUI → CodeWhale 身份链。
- 主 Agent：复核全部关键结论，并审计 Chaos4 当前 README、Feature 契约、命令注册表、TUI、策略、验证记录和工作树状态。

证据优先级：

1. 固定提交源码、正式改名 / 发布记录和当前官方产品文档。
2. 官方仓库 README、架构文档和官方截图。
3. 官方仓库 issue 只用于标识缺口，不把 issue 当作已交付契约。
4. 搜索摘要、第三方测评、营销转载和缓存页面不作为核心事实依据。

所有外部事实均以 2026-07-16 为访问快照。由于这些产品更新极快，报告不把当前模型 ID、星数、价格或预览功能当作长期稳定接口。

## 3. 总体设计版图

### 3.1 产品定位

| 产品 | 主要 UI 形态 | 设计中心 | 最有价值的模式 | 最需要警惕的点 |
| --- | --- | --- | --- | --- |
| Amp | 单 Thread Transcript + Composer + 可选 Agent / Thread 侧栏 | 强意见、前沿模型、Thread 可分享、长任务远程运行 | 全局命令面板；queue / steer / interrupt | 默认工具不审批；云端分享与插件执行面 |
| Codex CLI | 克制的滚动 Transcript + Picker + diff / permissions 视图 | 本地编码循环、安全层分离、AGENTS.md | 沙箱 × 审批 × diff × review | 能力变化快；不具备 Claude 式完整文件 rewind 契约 |
| Claude Code | 滚动模式 + 可选 fullscreen + Transcript Viewer | 长任务、后台工作、checkpoint、回溯 | Ctrl+O Transcript、Ctrl+B 后台、双 Esc rewind | 功能和配置面复杂；fullscreen 仍属预览 |
| uv-agent | ANSI 单列 Transcript + 底部 Composer + 全屏面板 | 单一 Python 出口、可审计脚本、渐进上下文 | run_python 记录、Thread / Skill / MCP mention | 文档与源码漂移；任意 Python 边界过宽 |
| CodeWhale | Ratatui Header / Transcript / Composer / Sidebar / Footer | 开放模型优先、责任与证据、回滚、Provider 路由 | Plan / Agent / YOLO；side-git / restore | 功能面过宽；YOLO、daemon / fleet 的治理成本 |
| Chaos4 当前工作树 | Windows 原生滚屏、单列 Transcript、底部动态尾部 | Windows-first、typed tools、任务真相、显式审批 | CommandRegistry、前台任务、steering 证据、semantic checkpoint 与只读 rewind preview | 无 OS 沙箱；无 rewind apply / restore |

### 3.2 共同交互闭环

~~~mermaid
flowchart LR
    A["用户在 Composer 输入"] --> B["上下文与项目规则"]
    B --> C["模型计划或工具提案"]
    C --> D{"策略是否允许"}
    D -->|"允许"| E["执行 typed tool / command"]
    D -->|"需要决定"| F["可见审批"]
    D -->|"拒绝"| G["拒绝证据"]
    F -->|"允许一次"| E
    F -->|"拒绝"| G
    E --> H["Transcript 事件与审计"]
    H --> I["Diff / Artifact / Test evidence"]
    I --> J{"完成条件满足"}
    J -->|"否"| C
    J -->|"是"| K["任务完成"]
    A -. "忙碌时 queue / steer / interrupt" .-> C
~~~

成熟产品的差异不在“有没有聊天框”，而在这条闭环中哪些状态可见、哪些结论有证据、哪些动作可以回滚。

### 3.3 功能矩阵

图例：● 为官方当前能力；◐ 为条件性、预览或部分能力；— 为官方未承诺或当前 Chaos4 明确不提供。

| 能力 | Amp | Codex CLI | Claude Code | uv-agent | CodeWhale | Chaos4 当前 |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| 可搜索命令面 | ● | ● | ● | ● | ● | ● |
| <code>@</code> 文件选择 | ● | ● | ● | ● | ● | — |
| <code>@@</code> Thread 引用 | ● | ◐ | — | ● | ◐ | — |
| queue / steer / interrupt 分离 | ● | ● | ◐ | ◐ | ● | ● |
| 可重映射快捷键 | ● | ● | ● | — | — | — |
| 外部编辑器编辑 Prompt | ● | ● | ● | — | ● | — |
| 完整 diff 视图 | ◐ | ● | ● | ◐ | ● | ◐ |
| 按 Turn 文件 checkpoint / rewind | — | — | ● | ◐ | ● | ◐（任意 checkpoint 只读 preview；无 apply） |
| resume / continue / fork / branch | ◐ | ● | ● | ● | ● | ◐ |
| 语义压缩与可追溯 Anchor | ◐ | ◐ | ◐ | ● | ◐ | ●（已接 Runtime） |
| 后台进程 / 任务视图 | ● | ● | ● | ● | ● | — |
| 子 Agent 可观察与切换 | ◐ | ● | ● | ◐ | ● | ◐ |
| Skills 延迟加载 | ● | ● | ● | ● | ● | ● |
| MCP | ● | ● | ● | ● | ● | ● |
| 插件贡献 UI / 命令 / 工具 | ● | ● | ● | ● | ● | 工具 live，其余待 Host 接线 |
| OS 级沙箱 | 依赖隔离 / 插件 | ● | ● | — | ● | — |
| 非交互 JSONL | ● | ● | ● | ◐ | ● | ● |
| Windows 明确支持 | ● | ● | ● | ● | ● | ●，第一目标平台 |
| 默认私有、本地状态优先 | ◐ | ● | ● | ● | ● | ● |

表中的“有 / 无”不是质量评分。例如后台 daemon 很强，但它也引入进程恢复、凭据、并发写入和远程攻击面；对 Chaos4 当前产品阶段不是自动加分项。

## 4. 产品逐项分析

### 4.1 Amp

#### 设计思想

Amp 把 Thread 当作完整工作记录，并以“强意见、持续追随新模型、不为旧行为保兼容”作为产品方向。四档 mode 表达任务难度、成本和能力，而不是权限；具体底层模型会变化，因此 Thread 审计必须记录实际模型与策略快照。[Amp 手册][AMP-MANUAL] [Amp mode][AMP-MODE]

#### UI 与交互

- <code>Ctrl+O</code> 是核心控制面。官方明确主张 Command Palette，而不是让命令文本污染 Prompt；输入 <code>/</code> 也会打开同一面板。
- Transcript 中 thinking / tool blocks 可折叠；<code>Alt+T</code> 切换详情。
- <code>Ctrl+\</code> 显示、聚焦或隐藏 Thread / Agent 侧栏。
- 忙碌时的新消息默认 queue；双 Enter 在当前工具或思考步骤结束后 steer；双 Esc 强制打断并立即发送。
- <code>Ctrl+G</code> 用外部编辑器编辑 Prompt，<code>Ctrl+R</code> 搜索历史，<code>@</code> 选择文件，<code>Ctrl+V</code> 粘贴图片。
- 完整键位由 <code>amp config keymap</code> 自描述，并允许配置。[Amp CLI 与键位][AMP-CLI] [Amp Command Palette][AMP-PALETTE]

![Amp CLI mode dial](https://static.ampcode.com/news/dial-release.png?v=20260709-2)

*图 1：Amp 官方 mode dial 截图。具体模型映射会变，值得借鉴的是“语义 mode + 实际模型快照”，不是照抄模型名。[来源][AMP-MODE]*

#### 会话、Agent 与上下文

- Thread 可保存、归档、搜索、引用和分享，<code>@@</code> 用于引用其他 Thread。
- 子 Agent 有独立上下文，适合并行调查和压缩主 Thread；但子 Agent 之间不能通信，用户不能中途指导，主 Agent 通常只得到最终摘要。
- 新架构强调 durable execution、远程控制和自动压缩；这些能力同时扩大了云端状态和远程攻击面。[Amp Tools / Subagents][AMP-TOOLS] [Amp Rebuilt][AMP-REBUILT]

#### 工具、插件与安全

Amp 默认不会在运行工具前询问批准。权限可通过 TypeScript 插件的生命周期事件定制，插件还可贡献工具、命令、UI、mode 和子 Agent。其扩展性很强，但插件代码直接在宿主中执行，官方也要求只使用可信插件。[Amp Permissions][AMP-PERMISSIONS] [Amp Plugins][AMP-PLUGINS]

#### 非交互接口

<code>amp -x / --execute</code> 支持单次执行，<code>--stream-json</code> 输出逐行 JSON，<code>--stream-json-input</code> 可保持持续程序化对话；runner 模式可在不打开 TUI 时接受远程 Thread。[Amp CLI][AMP-CLI]

#### 对 Chaos4 的取舍

应采用：

- 单一 Command Palette 作为控制平面。
- queue / steer / interrupt 的不同语义和持久证据。
- Thread 搜索、引用、归档和明确可见性。
- mode 使用用户可理解的难度 / 成本语言，同时保存真实 profile、model、prompt policy 和工具集。
- Skills 按需激活，MCP 工具不要全部常驻上下文。

不应采用：

- 默认无审批。
- 插件裸执行宿主 TypeScript / Python / shell。
- workspace 默认共享。
- “不保兼容”的配置与键位策略。
- 只保存压缩摘要而没有结构化 checkpoint 和原始事件索引。

### 4.2 Codex CLI

#### 设计思想

Codex CLI 的交互面相对克制：用户输入、工具活动、diff、审批和回复沿 Transcript 展开；其最清晰的设计是把任务模式、OS 沙箱和“何时询问用户”的审批策略分开。项目规则原生采用层级 <code>AGENTS.md</code>。[Codex CLI][CODEX-CLI] [Codex 安全][CODEX-SECURITY] [Codex AGENTS.md][CODEX-AGENTS]

![Codex CLI splash](https://raw.githubusercontent.com/openai/codex/main/.github/codex-cli-splash.png)

*图 2：OpenAI 官方仓库中的 Codex CLI 启动画面。[来源][CODEX-REPO]*

#### UI 与命令

- <code>/</code> 打开 Slash / Command Picker，<code>@</code> 搜索文件，<code>!</code> 运行本地 shell。
- 运行中按 Tab 排队下一 Turn，Enter 向当前 Turn 注入新指令。
- 空 Composer 双 Esc 可编辑上一条用户消息并从该点 fork。
- 当前命令面覆盖 <code>/permissions</code>、<code>/model</code>、<code>/plan</code>、<code>/goal</code>、<code>/diff</code>、<code>/review</code>、<code>/compact</code>、<code>/resume</code>、<code>/fork</code>、<code>/agent</code>、<code>/mcp</code>、<code>/skills</code>、<code>/statusline</code>、<code>/theme</code> 等。
- <code>/statusline</code> 可以选择和排序 model、context、rate limit、Git、token、session 等字段；<code>/keymap</code> 和 Vim 模式提高了键位可发现性。[Codex Developer Commands][CODEX-COMMANDS] [Codex CLI 定制][CODEX-CUSTOM]

#### Diff、Review 与会话

- <code>/diff</code> 展示 staged、unstaged 和 untracked 变更，避免漏审新文件。
- <code>/review</code> 提供工作树评审；非交互 <code>codex review</code> 可选择 base、commit 或 uncommitted。
- <code>/resume</code>、<code>/fork</code>、<code>/compact</code> 支持持续工作；当前官方契约没有 Claude Code 等价的“按每次提示恢复文件和会话”的完整 rewind。[Codex Developer Commands][CODEX-COMMANDS]

#### 安全模型

Codex 把两层控制明确分开：

1. Sandbox 决定 OS 层面能读写哪里、是否能联网。
2. Approval 决定何时必须问用户。

常见沙箱包括 read-only、workspace-write 和 danger-full-access；权限可在 TUI 中通过 <code>/permissions</code> 调整。这个分层非常适合 Chaos4，但不能把 hook 当作唯一安全边界，官方文档也记录了部分 hook 拦截范围限制。[Codex Sandbox][CODEX-SANDBOX] [Codex 安全][CODEX-SECURITY] [Codex Hooks][CODEX-HOOKS]

#### 扩展与自动化

Codex 支持 Skills、MCP、插件、hooks 和子 Agent；<code>codex exec --json</code> 面向自动化输出 JSONL。Windows 有 native sandbox 路径，也提供 WSL 建议。[Codex Skills][CODEX-SKILLS] [Codex MCP][CODEX-MCP] [Codex Subagents][CODEX-SUBAGENTS] [Codex 非交互][CODEX-EXEC]

#### 对 Chaos4 的取舍

应采用：

- sandbox、approval、mode 三层分离的概念模型。
- diff 必须包含 untracked，并支持 working tree / staged / per-turn 投影。
- <code>AGENTS.md</code> 继续作为唯一项目契约，不制造第二套规范。
- <code>/status</code> 展示 writable roots、模型、权限、上下文和 Session ID。
- TUI 与 JSONL 共用同一事件模型。

需要谨慎：

- 不把当前官方 Slash 命令表当成永久协议。
- 长时训练 / GPU 作业必须写持久日志，不能假定 TUI 会无损保留全部实时 stdout。
- danger-full-access 和 never-ask 不能成为默认开发姿态。

### 4.3 Claude Code

#### 设计思想

Claude Code 把终端做成长期工作的控制台。默认仍可使用滚动式交互，同时提供可选 fullscreen renderer；其优势不只是“面板更多”，而是 checkpoint、Transcript Viewer、后台工作、任务清单和 diff 组成了可恢复闭环。[Claude 交互模式][CLAUDE-INTERACTIVE] [Claude Fullscreen][CLAUDE-FULLSCREEN]

![Claude Code terminal demo](https://raw.githubusercontent.com/anthropics/claude-code/main/demo.gif)

*图 3：Anthropic 官方仓库的 Claude Code 终端演示。截图属于展示材料，不作为像素级规范。[来源][CLAUDE-REPO]*

#### UI 与输入

- <code>Ctrl+O</code> 打开 Transcript Viewer，可展开工具和 MCP 详情；在 fullscreen 中可把全文写回终端原生 scrollback 或交给外部编辑器。
- <code>Ctrl+B</code> 把 Bash / Agent 转后台，<code>Ctrl+T</code> 显示任务清单，<code>/tasks</code> 管理后台任务。
- 双 Esc 在空输入时打开 rewind；有草稿时清空并保留到历史。
- <code>Shift+Tab</code> 循环权限模式，<code>Alt+P</code> 切模型，<code>Alt+T</code> 控制 thinking。
- <code>/</code> 是命令 / Skill，<code>!</code> 是 shell，<code>@</code> 是文件。
- Windows / WSL 图片粘贴使用 <code>Alt+V</code>；<code>Ctrl+J</code> 是跨终端稳定的换行方式。[Claude 交互模式][CLAUDE-INTERACTIVE]

#### 权限

当前官方模式包括 default / Manual、acceptEdits、plan、auto、dontAsk 和 bypassPermissions。deny、ask、allow 规则可叠加；protected paths 在大多数模式下保持保护。Plan 是只读探索，计划通过后再选择接受编辑、自动执行或逐项审阅。[Claude Permission Modes][CLAUDE-MODES] [Claude Permissions][CLAUDE-PERMISSIONS]

#### Diff、checkpoint 与分支

- <code>/diff</code> 是交互式 viewer，可在当前 Git diff 与单 Turn diff 间切换。
- 每次用户提示建立 checkpoint；<code>/rewind</code> 可恢复代码、对话或两者，也可从目标消息开始摘要。
- <code>/resume</code>、<code>/branch</code> / fork 支持探索不同方向。[Claude Commands][CLAUDE-COMMANDS] [Claude Checkpointing][CLAUDE-CHECKPOINT]

#### 扩展

Claude Code 提供 settings scope、hooks、MCP、Skills 和自定义 subagent。能力丰富，但相应地增加了模式、配置层、规则优先级和治理成本。<code>CLAUDE.md</code> 属于模型上下文，不是强制安全策略；若 Chaos4 兼容 Claude Code，只应使用一个很薄的 <code>CLAUDE.md</code> 导入根 <code>AGENTS.md</code>，避免两套规范漂移。[Claude Settings][CLAUDE-SETTINGS] [Claude Memory][CLAUDE-MEMORY] [Claude Hooks][CLAUDE-HOOKS]

#### 对 Chaos4 的取舍

应采用：

- Transcript Inspector，而不是把所有原始详情常驻主 Transcript。
- 按用户 Turn 建 checkpoint，并让 code / conversation / both 三种 rewind 语义明确分开。
- 后台任务有 ID、状态、输出尾部和可取消动作。
- diff 支持 per-turn 视角。

不应采用：

- 默认切换到 fullscreen；Chaos4 的 Windows 原生 scrollback 已是明确产品选择。
- 一次引入全部 settings / hooks / workflow / cloud 表面。
- 把模型记忆或 Prompt 规则当作权限边界。

### 4.4 uv-agent

#### 设计思想

uv-agent 的核心实验是：模型只有一个外部动作 <code>run_python</code>，每次调用是一段由 <code>uv run</code> 管理的完整 Python 脚本；脚本可调用文件、搜索、进程、图片和插件辅助能力，运行记录持久化以便审计与重放。项目明确标注 experimental。[uv-agent README 固定提交][UV-README] [uv-agent Runtime][UV-RUNTIME]

这是一种很好的“可观察边界”研究，但不是自动安全：

- 单一出口减少了模型工具选择和协议数量。
- 该出口内部仍可使用 Python 库、子进程和插件，因此权限半径可能比 typed tools 更大。
- 如果运行环境不是 OS sandbox，单工具名称无法约束间接文件或网络效果。

#### TUI

固定发布提交展示的 TUI 是 ANSI 单列 Transcript、底部 Composer、紧凑状态尾部，并带命令面、文件 <code>@</code>、Thread <code>@@</code>、Thread 恢复、运行详情 pager、图片和 Agent View。[uv-agent TUI 文档][UV-TUI]

![uv-agent TUI](https://raw.githubusercontent.com/uv-agent/uv-agent/3567abecbcd5bd85283d526d9dac4f1f1f081c8b/docs/t2.png)

*图 4：uv-agent v0.21.4 固定提交的官方 TUI 截图。[来源][UV-SCREENSHOT]*

#### 契约漂移风险

在 v0.21.4 固定提交中，README 展示 <code>uv-agent ask</code>，但 argparse 源码的进程级 choices 只有 TUI、daemon 和内部 workflow-node；同一提交的 <code>docs/tui.md</code> 与按键源码对 Enter / Ctrl+Enter 的提交与换行语义也不一致。[uv-agent CLI 源码][UV-CLI-SOURCE] [uv-agent 键位源码][UV-KEY-SOURCE]

这对 Chaos4 是直接教训：命令帮助、Picker、README 和测试必须从同一个 <code>CommandRegistry</code> / Keymap Catalog 生成，不能人工维护四份表。

#### 上下文与插件

uv-agent 的渐进式披露、Goal 持久状态、项目共享脚本环境、结构化事件和 NetGain compaction 值得研究；插件是可信 Python 包，可贡献 Runtime namespace、命令、UI、事件和持久存储，因此仍需供应链信任。[uv-agent Plugins][UV-PLUGINS]

#### 对 Chaos4 的取舍

应采用：

- 运行详情可按 ID 回看。
- Skills / MCP / Thread 只在需要时披露。
- compact 决策可用成本和历史依赖衡量，但必须 A/B 验证。
- 证据、任务状态和可重建 Runtime Context 不应被普通消息摘要吞掉。

不应采用：

- 用任意 <code>run_python</code> 替换现有 typed tools 和 <code>ActionPolicy</code>。
- 让 trusted plugin 直接获得宿主全部权限。
- 在命令 / 按键契约未统一时快速扩表。

### 4.5 DeepSeek-TUI / CodeWhale

#### 政名与产品定位

正式名称是 CodeWhale。v0.8.41 起发布 <code>codewhale</code> 和 <code>codewhale-tui</code>，旧二进制作为过渡 shim；旧状态目录保留读取兼容。[CodeWhale 政名文档][CW-REBRAND]

CodeWhale 当前以 Rust / Ratatui 构建终端 Agent Harness，强调 Provider 路由、审批、OS 沙箱、会话、子 Agent、MCP、Skills、Runtime API 和按轮回滚。[CodeWhale README][CW-README] [CodeWhale 架构][CW-ARCH]

![CodeWhale TUI](https://raw.githubusercontent.com/Hmbown/CodeWhale/9035890c464b1815bc592fc5f4b00d0fe7837ffb/assets/screenshot.png)

*图 5：CodeWhale 固定提交的官方 TUI 截图。[来源][CW-SCREENSHOT]*

#### UI 与模式

官方用户指南把界面分成 Header、Transcript、Composer、Sidebar 和 Status / Footer。Plan、Agent、YOLO 是可见 TUI mode；approval mode 与模型路由另行控制。状态栏可选择 mode、model、cost、context、Git branch、tokens 等项目。[CodeWhale Guide][CW-GUIDE] [CodeWhale Modes][CW-MODES]

#### 工具与回滚

- structured file / search / Git / diagnostics tools 优先于 shell；长尾任务才走 shell。
- side-git 快照位于仓库 <code>.git</code> 之外，<code>/restore</code> 回滚文件，不改写会话历史。
- fork、双 Esc backtrack、文件 restore 是三条不同恢复路径。
- 子 Agent 返回 compact receipt 和 transcript handle，验证默认是 self-report，仍需独立 gate。[CodeWhale Tool Surface][CW-TOOLS]

#### 对 Chaos4 的取舍

应采用：

- mode、approval、model 三者明确分离。
- 每次写入前建立可验证文件快照，回滚不改写用户 Git 历史。
- 子 Agent receipt 包含运行 ID、usage、artifact 和 verification ownership。
- 状态栏按宽度收缩，永远不挤掉 Composer。

不应采用：

- 把 YOLO / Full Access 当作主要体验。
- 在当前阶段加入 Fleet、daemon、移动端和大量 Provider UI。
- 让隐藏 side-git 快照覆盖启动前 dirty baseline。
- 让 repo Prompt 文件扩大权限；repo 规则只能收紧，不能放宽宿主 policy。

## 5. 横向设计结论

### 5.1 最佳模式来源

| 设计问题 | 最佳参考 | Chaos4 的适配方式 |
| --- | --- | --- |
| 命令发现 | Amp | <code>Ctrl+O</code> 与 <code>/</code> 打开同一 Picker；不把命令写入模型 Prompt |
| 安全分层 | Codex | mode、model、sandbox、approval 四类状态独立显示 |
| 长任务恢复 | Claude Code | 每个用户 Turn 建 checkpoint；code / conversation / both 明确分开 |
| 单列 ANSI 性能 | uv-agent | 已完成内容追加到原生 scrollback，只重绘动态尾部 |
| 文件回滚 | CodeWhale | repo <code>.git</code> 外的有界快照；不覆盖 dirty baseline |
| 子 Agent 真相 | Chaos4 当前契约 | child 结果 advisory；独立 verifier 才能形成完成证据 |
| 扩展安全 | Chaos4 当前插件模型 | 声明式 manifest、digest trust、风险地板、Host typed mapping |

### 5.2 应拒绝的“功能越多越好”思路

- 全屏界面不天然优于原生 scrollback；它会接管选择、复制、搜索和终端兼容。
- 单一工具不天然安全；要看该工具内部实际可达能力。
- 自动压缩不天然可靠；要看摘要是否有 source anchor 和原始事件恢复路径。
- 子 Agent 数量不等于完成质量；并行写同一目录会制造冲突，子 Agent 自报测试不能代替独立验证。
- “自动执行”不等于无人值守安全；daemon、远程控制和自动发布需要完全不同的威胁模型。
- screenshot 是展示材料，不是可用性研究；本报告不从像素样式推断产品稳定性。

## 6. Chaos4 当前状态审计

### 6.1 版本与证据边界

- <code>pyproject.toml</code> 当前声明版本 1.0.2，入口是 <code>chaos-agent = code_agent_win.cli:main</code>。[项目清单](../../pyproject.toml)
- 当前工作树在研究开始前已有大量未提交修改和未跟踪文件。本报告保留这些用户工作，不把工作树能力冒充已发布 1.0.2 契约。
- [Amp-inspired Runtime 记录](../amp-inspired-runtime.md) 分别保留 2026-07-15 的 554 项历史自动化测试快照和 2026-07-19 的 P0 recovery 验收。
- [Terminal Capabilities 验证记录](../releases/1.1.0-terminal-capabilities-validation.md) 是另一个针对终端 / Profile / Skills / MCP 的验收切片。两份计数口径不同，不能直接相加。
- 2026-07-16 初稿没有重跑源码测试；后续 P0 recovery 是独立实施与验收切片，Feature 共运行 530 项，其中 526 项通过、4 项平台 skip；root integration 163 项全部通过。自动化合计运行 693 项，其中 689 项通过、4 项 skip。它仍不是已发布 1.0.2 的替代声明。

### 6.2 已经形成的正确方向

| 区域 | 当前事实 | 评价 |
| --- | --- | --- |
| 终端架构 | Windows 原生 scrollback、追加式 Transcript、一个底部 Composer、Picker 在其上 | 应保留 |
| 视觉语义 | Unicode、中等留白、青色强调、灰色工具记录、绿色仅用于任务完成 | 清晰且可访问性较好 |
| 模式 | low / medium / high / ultra 冻结 profile、模型、Prompt、工具和限制 | 正确；模式不授予权限 |
| 权限 | typed tool → central policy → explicit approval；默认 No | 应保留并继续可视化 |
| Steering | queued / steered / dequeued / applied 由持久事件证明 | 比单纯 UI 文案更可靠 |
| 前台任务 | pause / resume / stop / steer，重启后从 checkpoint 恢复，不重放 in-flight command | 状态真相明确 |
| Skills | <code>.agents/skills</code>、digest 冲突隔离、显式激活 | 安全面优于任意脚本 |
| MCP | approved stdio、风险映射、统一 ActionPolicy | 正确 |
| 插件 | declarative manifest、SHA-256 trust、Host typed mapping | 应坚持，不转为裸代码 |
| Diff | staged / unstaged / untracked / per-turn / since-checkpoint 的有界事实与 scope-aware 只读投影 | P0 审阅视角已交付 |
| 子 Agent | typed action、取消 / 预算 / 并发、结果 advisory | 正确 |
| Context | 真实 `ContextRequest(thread_id, revision, ...)`、数值预算、source-anchored compaction | 已接 Runtime，不伪造 Thread 身份 |
| Rewind | checkpoint candidate 分页；conversation / code / both 双观测只读 preview | P0 仅预览；无 apply / restore |

本表的本地事实来自 [README](../../README.md)、[interfaces Feature 契约](../../src/code_agent/interfaces/AGENTS.md)、[Windows Integration 契约](../../code_agent_win/AGENTS.md)、[CommandRegistry](../../src/code_agent/interfaces/command_registry.py)、[P0 Recovery Plan](../superpowers/plans/2026-07-16-cli-tui-p0-recovery-review-plan.md) 和 [Amp-inspired Runtime 记录](../amp-inspired-runtime.md)。

### 6.3 P0 交付与剩余缺口

| 优先级 | 缺口 | 影响 |
| --- | --- | --- |
| P0 已交付 | 真实 <code>ContextRequest</code> 身份与 Source Anchor | semantic checkpoint 已诚实接入 Runtime |
| P0 已交付（只读） | 完整 capture coverage、外置 snapshot 与任意 checkpoint rewind preview | 可验证 conversation / code / both 影响；不 apply 或 restore |
| P0 已交付 | staged / unstaged / untracked / per-turn / since-checkpoint 统一审阅事实 | 新文件与跨 Turn 变更进入有界视图 |
| P1 | 没有自描述、可重映射 Keymap Catalog | 文档、快捷键和实际 handler 可能漂移 |
| P1 | 没有 <code>@</code> 文件和 <code>@@</code> Thread Picker | 用户需要手工复制路径 / ID，发现成本高 |
| P1 | 缺少 Transcript / Run Inspector | 详情要么挤进主 Transcript，要么难以追溯 |
| P1 | 子 Agent 缺少完整可观察、切换和中途 steer 视图 | 并行工作仍像黑盒 |
| P2 | 无 native OS sandbox | typed policy 能控制直接调用，但不能隔离项目代码的间接副作用 |
| P2 | 无进程级后台 job manager | 长时训练 / server 只能占用前台或依赖外部终端 |
| 明确暂缓 | daemon、远程 observer、自动 worktree / commit / push | 当前产品契约明确不提供，不能通过 UI 功能顺手引入 |

## 7. 推荐的 Chaos4 目标界面

### 7.1 核心原则

1. Transcript 是审计历史，不是动画画布。
2. 已完成内容只追加一次；只有 Composer、状态、Picker、审批和运行尾部动态重绘。
3. 一屏只突出一个决策：输入、审批、diff、恢复或错误。
4. 色彩只增强语义，状态必须同时有文字和符号。
5. 主界面保持单列；宽屏 inspector 是可选投影，不改变 Runtime。
6. 所有状态词必须由持久事件或 Runtime reducer 推导。

### 7.2 默认布局

~~~text
┌─ Windows Terminal 原生滚屏 ───────────────────────────────────────────────┐
│ 你   修复配置迁移，并运行最小测试                                          │
│                                                                            │
│ ◇ 计划  3 个步骤                                                           │
│ ↳ read_file  src/.../loader.py                         42 ms              │
│ ↳ apply_patch  1 file  +8 -2                           17 ms              │
│ ▸ 工具详情已折叠 · /show run_01                                           │
│                                                                            │
│ Δ 1 file  +8 -2   未跟踪 0   验证待运行                  /diff             │
│                                                                            │
│ [需要决定] run_command                                                     │
│ 目标: python -m unittest ...                                               │
│ 风险: 执行项目代码    原因: 验证本次变更                                   │
│   [默认: 否]  [允许一次]  [编辑命令]                                       │
├────────────────────────────────────────────────────────────────────────────┤
│ /命令、@文件、@@会话                                                       │
│ ┌────────────────────────────────────────────────────────────────────────┐ │
│ │ 输入消息…                                                              │ │
│ └────────────────────────────────────────────────────────────────────────┘ │
│ 运行中 · 2/3 · 已排队 1        high · ask · gpt-… · ctx 63% · 00:41       │
└────────────────────────────────────────────────────────────────────────────┘
~~~

这不是 alternate-screen 的固定大框；上图只是信息层级。实际实现继续把完成内容写入 Windows Terminal 正常缓冲区，并只重绘底部动态尾部。

### 7.3 宽度降级

| 终端宽度 | 行为 |
| --- | --- |
| ≥ 120 列 | 可选右侧 Inspector；状态显示 mode、permission、model、context、elapsed |
| 80–119 列 | 单列；状态左右两组，隐藏低优先级 token / rate |
| 50–79 列 | Picker 与审批改为逐行；路径缩短但可展开；状态只保留 task、permission、elapsed |
| < 50 列 | plain / ASCII 安全布局；不渲染表格边框；所有操作仍可完成 |

### 7.4 状态栏优先级

从高到低：

1. waiting approval / failed / paused / verifying / running。
2. queue 数和当前步骤。
3. permission profile。
4. task mode 与实际 model / profile。
5. context pressure。
6. elapsed、token、rate limit。

空间不足时从 6 向 1 隐藏，不能反过来。

## 8. 推荐交互规范

### 8.1 键位

| 键位 | Idle | Running | Modal / Picker |
| --- | --- | --- | --- |
| Enter | 发送 | steer 当前 Turn | 确认当前选择 |
| Ctrl+J | 插入换行 | 插入换行 | 不抢占 |
| Tab | 完成 / 接受候选 | queue 下一 Turn | 下一个控件 |
| Ctrl+O | 打开统一命令面 | 打开只读命令面 | 关闭后返回原 modal |
| Ctrl+R | 搜索 Prompt 历史 | 搜索，不中断任务 | modal 自己处理 |
| Ctrl+G | 外部编辑 Composer | 编辑 queued draft | 编辑可编辑字段 |
| Alt+T | 展开最近工具 / reasoning | 切换运行详情 | 不抢占 |
| Esc | 清空草稿或取消最上层 UI | 请求 pause / interrupt | 关闭最上层 UI |
| Ctrl+C | 按现有状态机中断并武装退出 | 中断当前工作 | 拒绝 / 关闭 |
| Ctrl+C 再次 | 两秒窗口内退出 | 两秒窗口内退出 | 两秒窗口内退出 |
| @ | 文件 Picker | queued draft 文件 Picker | 当前 Picker 的过滤字符 |
| @@ | Thread Picker | queued draft Thread Picker | 当前 Picker 的过滤字符 |

保留 Chaos4 已验证的双 <code>Ctrl+C</code> 退出守卫，不照搬其他产品把 <code>Ctrl+C</code> 同时承担太多隐式语义。

### 8.2 统一命令面

当前 [CommandRegistry](../../src/code_agent/interfaces/command_registry.py) 已经统一 parse / help / palette / filter，应扩展而不是另建 Slash Parser。

推荐分组：

| 分组 | 命令 |
| --- | --- |
| 会话 | <code>/新建</code>、<code>/会话</code>、<code>/恢复</code>、<code>/分支</code>、<code>/压缩</code> |
| 工作 | <code>/任务</code>、<code>/暂停</code>、<code>/继续</code>、<code>/停止</code>、<code>/引导</code>、<code>/代理</code> |
| 审阅 | <code>/差异</code>、<code>/回退</code>、<code>/证据</code>、<code>/追踪</code>、<code>/显示</code> |
| 控制 | <code>/模式</code>、<code>/权限</code>、<code>/模型</code>、<code>/上下文</code> |
| 扩展 | <code>/工具</code>、<code>/技能</code>、<code>/mcp</code>、<code>/插件</code> |
| 显示 | <code>/主题</code>、<code>/颜色</code>、<code>/字形</code>、<code>/语言</code>、<code>/快捷键</code> |
| 系统 | <code>/状态</code>、<code>/诊断</code>、<code>/帮助</code>、<code>/清屏</code>、<code>/退出</code> |

每个命令必须具备：

- 中文主名与稳定英文 alias。
- 分组、简述、usage、required capabilities。
- active-task 可用性。
- 禁用原因和恢复建议。
- JSON / TUI 是否可用。
- 帮助、README 命令表和 golden test 的同源生成。

### 8.3 Approval Card

~~~text
需要决定
动作     run_command
来源     main agent / plugin id / MCP server
目标     规范化后的绝对或工作区相对目标
风险     network / write / shell / critical
原因     模型提案 + Host 分类理由
影响     预计文件、外部端点或命令
权限     ask（mode 不会修改此项）

> 否（默认）   允许一次   编辑输入   查看规则
~~~

规则：

- 默认选择永远是 No。
- <code>Esc</code> 等价拒绝，不等价后台执行。
- 允许一次不写入持久 allowlist。
- “本会话允许”只对精确 typed action + 规范化目标生效。
- critical、发布、删除、凭据和外部写入不得被宽泛目录规则静默放行。
- 插件声明风险和 Host 映射风险取更高值。

## 9. 模式、权限、模型与任务状态

### 9.1 四个轴

| 轴 | 回答的问题 | 示例 |
| --- | --- | --- |
| Task mode | 这项任务要用多少能力、成本和步骤？ | low / medium / high / ultra |
| Model snapshot | 实际调用了谁、用何协议和 reasoning？ | profile、provider、model、API、effort |
| Permission profile | 哪些动作能直接运行，哪些必须问？ | plan / ask / elevated / full-local |
| Task state | 当前真实发生了什么？ | running / approval / verifying / paused / failed |

任何 mode 切换都不能扩大权限；任何 model 切换都不能改变 task state；任何 UI 绿色“完成”都必须由 task reducer 和 verification evidence 支持。

### 9.2 推荐权限矩阵

下表是用户界面语义建议；为兼容当前配置，可保留既有内部值。

| 动作 | Plan / 只读 | Ask / 标准前台任务 | Elevated | Full-local |
| --- | --- | --- | --- | --- |
| 工作区读取 | 允许 | 允许 | 允许 | 允许 |
| 工作区 typed write | 拒绝 | 允许，必须 snapshot + diff | 允许，必须 snapshot + diff | 允许，必须 snapshot + diff |
| 结构化 verification | 拒绝或询问 | 允许已注册类型 | 允许已注册类型 | 允许已注册类型 |
| 原始 PowerShell | 拒绝 | 每次询问 | 每次询问 | 每次询问 |
| 工作区外读取 | 拒绝 | 单目标询问 | 每目标询问 | typed read 可允许 |
| 工作区外写入 | 拒绝 | 拒绝 | 每目标询问 | typed write 可允许 |
| 网络 | 拒绝 | 每端点 / 工具询问 | 每端点 / 工具询问 | 仍询问 |
| MCP / 插件副作用 | 拒绝或询问 | 按最高风险询问 | 按最高风险询问 | 按最高风险询问 |
| 删除 / 发布 / push / 生产 | 拒绝 | critical 单次确认或拒绝 | critical 单次确认 | critical 单次确认 |

Full-local 不等于 bypass policy，更不等于 YOLO。

## 10. Task、子 Agent 与后台工作

### 10.1 状态机

~~~mermaid
stateDiagram-v2
    [*] --> Queued
    Queued --> Running
    Running --> WaitingApproval
    WaitingApproval --> Running: allow
    WaitingApproval --> Paused: deny_or_escape
    Running --> Paused: pause
    Paused --> Running: resume
    Running --> Verifying
    Verifying --> Running: evidence_missing
    Verifying --> Completed: gates_pass
    Running --> Partial: budget_or_scope_boundary
    Running --> Failed: unrecoverable_error
    Running --> Interrupted: user_interrupt
    Queued --> Cancelled
    Paused --> Cancelled
    Partial --> Running: accepted_followup
    Completed --> [*]
    Failed --> [*]
    Interrupted --> [*]
    Cancelled --> [*]
~~~

### 10.2 子 Agent 规则

- 同一父任务默认一个 writer lease。
- explore / search / review / verifier 默认只读，可并行。
- implementer 必须获得不重叠目录或文件租约；冲突时排队，不靠 Prompt 自觉。
- 父 Agent 可查看 child 状态、最后事件、预算、artifact、输出 handle，并可 cancel / steer。
- 子 Agent 完成只产生 advisory receipt；独立 verifier 或父任务 gate 才能把结论升级为 evidence。
- child 原始长输出落到有界 artifact，父 Transcript 只显示摘要和 handle。

### 10.3 后台任务的最小版本

近期如果增加后台能力，应先做进程内 job manager，不立即做 daemon：

- <code>job_id</code>、command、cwd、start time、status、exit code、bounded tail。
- <code>/jobs list/show/wait/stdin/cancel</code>。
- TUI 退出后标记 stale，不谎称仍在运行。
- 训练 / server 的完整输出写用户明确的日志文件。
- daemon 恢复、远程批准、凭据代理和多进程 writer lock 留给单独设计。

## 11. Context、checkpoint 与 rewind

### 11.1 真实身份与语义 checkpoint 已接入

当前 Runtime 为每个 model turn 构造显式、不可变的请求对象：

~~~text
ContextRequest
  thread_id
  revision
  user_input
  tools
  task_state
  mode_snapshot
  permission_snapshot
  context_pressure
  cancellation
  timeout_seconds
  budget_lease
~~~

真实 <code>thread_id</code> 和正 revision 进入 ContextBuilder 与 semantic
compactor。Host 只持久化脱敏的 Source Anchor / checkpoint facts，并通过
coordinated Sessions repository 把 checkpoint 与 workspace mutation
high-water 排序；不会伪造 Thread 身份，也不会把 summary、source text 或
messages 写入 checkpoint payload。

### 11.2 完整 mutation capture 与 checkpoint 锚定

对 Host 可精确理解的 workspace typed edit，写侧顺序是：

1. 在共享 cross-process gate 内观察 exact before / planned after 状态。
2. 把 exact inverse bytes 保存到仓库外的产品状态目录。
3. 在文件变更前持久化 `PREPARED` mutation、路径 preimage 和 lineage。
4. 执行 edit，核对 exact postimage 后持久化 `COMPLETED`。
5. checkpoint 在同一 gate 内锚定 owner、coverage generation 与 mutation
   high-water，不能观察到过时的变更序列。

对于不能精确捕获文件效果的 writer，Host 必须在执行前持久化
`unknown-writer` `GAP`。该记录把当前 coverage 标记为 invalidated；后续
checkpoint 仍可做 conversation preview，但该 coverage 的 code facet
持续 fail closed。

### 11.3 rewind 的三种只读 facet

| Preview kind | Conversation facet | Code facet | 修改会话 / 文件 |
| --- | --- | --- | :---: |
| <code>conversation</code> | checkpoint 后经 thread-filtered 边界验证的 message 数 | 不选择 | 否 |
| <code>code</code> | 不选择 | 完整、排序后的路径与 baseline provenance | 否 |
| <code>both</code> | 同 conversation | 同 code | 否 |

`/rewind list [cursor]` 只列出候选；candidate 的 message-bound /
code-anchor facet 是发现提示，不是可用性承诺。
<code>/rewind preview &lt;checkpoint-id&gt; &lt;conversation|code|both&gt;</code>
至多执行两次完整观测，来源在观测间移动时重试并最终 fail closed。

Code preview 只有在完整 capture coverage、每个 mutation 的 exact inverse
snapshot / preimage、相邻同路径 continuity 和当前 workspace tip 全部验证
通过后才可用。只有该完整证据链成立时，UI 才声明保存了 pre-existing
user bytes；baseline provenance 本身不是证明强度。

当前 `/rewind` 没有 apply、restore、<code>git reset</code>、
<code>git checkout</code>、approval、provider 或 tool action。Snapshot
位于产品状态目录，不污染仓库或改写用户 Git 历史。

### 11.4 compaction

- project rules、tool schema、task facts、evidence ledger 和可重建 Runtime Context 不进入普通消息摘要。
- message compaction 可比较 deterministic baseline 与 NetGain / semantic 方法。
- A/B 指标至少包括：后续工具误用、重新读取次数、cache input、总成本、完成率、Source Anchor 命中率。
- 原始事件始终可按 anchor / run_id / turn_id 读取；摘要不是唯一真相。

## 12. Diff 与审阅

统一 DiffView 应支持：

1. working tree：staged、unstaged、untracked。
2. per-turn：只看某一 Turn 的写集合。
3. since-checkpoint：从选择的 checkpoint 到现在。
4. 文件统计：新增 / 删除行、二进制、重命名、未跟踪。
5. 导航：文件、hunk、路径过滤、窄屏。
6. 评论：评论只生成 review feedback，不直接执行修改。
7. NO_COLOR / ASCII 和超长行截断。
8. fresh Git reader 与 typed write record 的来源标签；两者不一致时必须提示 stale。

完成前的默认门：

~~~mermaid
flowchart TD
    A["实现结束"] --> B["刷新 working tree diff"]
    B --> C{"存在未审阅变更"}
    C -->|"是"| D["用户或 Review Agent 审阅"]
    C -->|"否"| E["运行独立 verification gate"]
    D --> E
    E --> F{"有可复现通过证据"}
    F -->|"否"| G["返回 Running / Partial"]
    F -->|"是"| H["Completed"]
~~~

## 13. CLI 与 JSONL 协议

### 13.1 保留当前稳定入口

近期不为追随上游而重命名：

~~~powershell
chaos-agent
chaos-agent ask "..."
chaos-agent resume THREAD_ID ["..."]
chaos-agent run --json "..."
chaos-agent task list
chaos-agent task resume TASK_ID ["..."]
~~~

如果未来增加 <code>exec</code>，应先作为 <code>run</code> 的兼容 alias，经过发布迁移期再决定主名。

### 13.2 JSONL

建议每行使用版本化 envelope：

~~~json
{
  "schema_version": 1,
  "event_id": "evt_...",
  "thread_id": "thr_...",
  "turn_id": "turn_...",
  "task_id": "task_...",
  "sequence": 42,
  "time": "2026-07-16T12:00:00+08:00",
  "type": "tool.completed",
  "payload": {}
}
~~~

约束：

- <code>--json</code> 时 stdout 只输出 JSONL；日志、诊断和进度噪声走 stderr。
- sequence 在 Thread 内单调，resume 后继续。
- payload 有界，长内容返回 artifact handle。
- 机密、API key、原始 Prompt 和敏感路径按 allowlist 输出。
- 末尾必须有 <code>turn.completed</code>、<code>turn.failed</code> 或 <code>turn.interrupted</code>。
- 退出码区分 usage、policy deny、provider、verification fail 和 internal error。

## 14. Skills、MCP 与插件

### 14.1 继续保持分层

| 机制 | 用途 | 权限 |
| --- | --- | --- |
| AGENTS.md | 仓库契约、边界、Unit 协作 | Prompt 指令，不授予权限 |
| Skill | 可复用工作流、参考与模板 | 延迟加载，不执行任意脚本 |
| MCP | 外部结构化工具 | server 需批准；每个工具有风险映射 |
| Plugin | 声明 Host 扩展 | digest trust、版本、namespace、risk floor |

### 14.2 插件能力模型

插件 manifest 应声明：

- id、version、host API version、digest、publisher / source。
- contributions：tool、command、picker item、status item、event subscription、mode metadata。
- capability requirements：read workspace、write workspace、network、MCP、UI。
- risk floor 和 mapped Host action。
- activation scope：user / workspace、session only / persistent（若未来有 daemon）。

外部插件不能直接：

- 写 ANSI / terminal escape。
- 启动任意进程。
- 请求任意 URL。
- 注册任意 Python callback。
- 绕过 ActionPolicy。
- 把 mode、Skill 或 UI 选择解释为 authorization。

## 15. Windows Terminal 与可访问性

### 15.1 兼容策略

- 正常缓冲区是默认；alternate-screen 只能显式开启。
- ConPTY、Windows Terminal、VS Code xterm.js、旧 Console Host 和 WSL 分别做能力检测。
- bracketed paste 保持当前 CRLF → LF、256 KiB 上限、永不自动提交。
- 不假定 Shift+Enter、Kitty keyboard protocol 或 Alt 组合在所有终端可辨识；<code>Ctrl+J</code> 作为稳定换行。
- SGR mouse 只用于应用内可控区域；原生选择和复制优先。
- 路径、命令、Git ref、模型和原始 tool facts 不翻译。
- 东亚宽字符、emoji、组合字符和超长无空格文本使用 display width，而不是 Python 字符数。

### 15.2 视觉与无障碍

- 保留当前“青色强调、灰色工具、绿色仅完成”的语义。
- 主要工具文字不使用 dim + dark gray 叠加。
- warning / error / approval 同时使用图标、文字和颜色。
- <code>NO_COLOR</code>、ASCII glyph 和 plain theme 必须功能等价。
- 每个折叠块显示摘要、数量和 <code>/show ID</code>，不能只写“更多”。
- 屏幕阅读器 / 日志模式可禁用动画和动态覆盖，改为逐事件纯文本。

## 16. 推荐架构

~~~mermaid
flowchart TB
    UI["Windows TUI / Text CLI / JSONL"] --> VM["共享 View Models"]
    VM --> CMD["CommandRegistry + KeymapCatalog + Picker"]
    VM --> VIEW["Transcript / Diff / Approval / Agent / Checkpoint Views"]
    CMD --> APP["Application Controller"]
    VIEW --> APP
    APP --> CORE["Core Turn + Task State Machines"]
    APP --> RW["RewindRuntime · list / preview only"]
    RW -->|"read only"| RSESS["Sessions bounded observation / coverage journal"]
    RW -->|"read only"| RSNAP["Validated snapshots / current file states"]
    RW --> VM
    CORE --> CTX["ContextRequest + Checkpoint + Source Anchors"]
    CORE --> ORCH["Orchestration + Writer Lease + Child Receipts"]
    CORE --> POLICY["ActionPolicy + Permission Profile"]
    POLICY --> TOOLS["Typed Host Tools"]
    TOOLS --> FILES["Workspace / Git / Verification / MCP"]
    FILES --> EVENTS["Versioned Event + Evidence Ledger"]
    EVENTS --> CORE
    EVENTS --> VM
~~~

关键约束：

- ViewModel 不执行工具。
- Plugin 不直接接 Core callback。
- Event 是 TUI、CLI、JSONL 和恢复的共同来源。
- Evidence Ledger 独立于模型消息 compaction。
- UI 可被替换，Task / Policy / Checkpoint 真相不变。
- RewindRuntime 只读取 observation、snapshot 和当前文件状态，不调用
  apply、restore、Git、approval、provider 或 tool action。

## 17. 分阶段落地路线

本路线遵循仓库“需求 → 实现 → 集成”和“目录即边界”的规则；每个 Feature 的主要 Unit 不超过 10 个。

### 阶段 A：需求契约

只更新对应 Feature 的目标与边界，不写实现：

| Feature | 建议边界 |
| --- | --- |
| interfaces | Unified Palette、Keymap Catalog、Inspector、Diff / Approval 投影；不执行工具 |
| thread_intelligence | ContextRequest、Checkpoint、Source Anchor、Thread search；不决定权限 |
| orchestration | child lifecycle、writer lease、receipt；不把 child 自报变成 evidence |
| policy | permission profile 和 approval decision；不渲染 UI |
| sessions | checkpoint / revision / artifact 持久化；不执行恢复动作 |

### 阶段 B：P0 已交付（preview-only）

1. ContextBuilder 已接收真实 <code>ContextRequest(thread_id, revision, ...)</code>。
2. semantic checkpoint / Source Anchor Units 已接 Runtime。
3. DiffView 已覆盖 untracked、staged / unstaged、per-turn 和 since-checkpoint。
4. WorkspaceSnapshot、完整 capture coverage 和三种 rewind preview facet
   已接入；apply / restore 未交付。
5. 关键路径已有 Unit、迁移、Runtime 与 TUI integration 测试。

### 阶段 C：P1 实现

1. <code>KeymapCatalog</code> 与 <code>/快捷键</code>。
2. <code>@</code> 文件和 <code>@@</code> Thread Picker。
3. <code>/显示 run_id</code> Transcript / Run Inspector。
4. 子 Agent 列表、详情、cancel / steer ViewModel。
5. README 命令表与 golden fixtures 从 registry 生成。

### 阶段 D：集成

只在集成阶段修改 <code>code_agent_win</code> 入口和根配置：

- 把新 ViewModel 接到 Windows TUI。
- 把 checkpoint / diff / child 事件接到 JSONL。
- 保留旧 Session 和配置迁移。
- 不在此阶段顺手修改 Feature 内接口；若发现问题，回退到对应实现阶段。

### 阶段 E：可选后续

- Native Windows sandbox / AppContainer 调研。
- 进程内 background jobs。
- 宽屏只读 Agent Inspector。
- daemon / remote / worktree 只在另一次明确需求评审后考虑。

## 18. 验收矩阵

| 层级 | 必测内容 |
| --- | --- |
| Unit | registry / keymap 唯一性、Picker 过滤、approval 默认 No、checkpoint revision、snapshot dirty baseline、diff 分类、child receipt |
| Reducer | queued → steered → dequeued → applied；approval；pause / resume；verification ownership |
| Persistence | SQLite migration、checkpoint / artifact 完整性、resume 不重放 in-flight command |
| PTY / Golden | 40 / 60 / 80 / 120 列、CJK 宽度、NO_COLOR、ASCII、粘贴、resize、双 Ctrl+C |
| Integration | typed write → snapshot → diff → verification → complete；MCP / plugin 最高风险 |
| JSONL | schema、顺序、stdout 纯净、恢复后 sequence、长输出 handle、secret redaction |
| Manual Windows | Windows Terminal、VS Code terminal、旧 Console / PowerShell、中文 IME、鼠标选择、休眠恢复 |
| Adversarial | untrusted repo 指令、MCP prompt injection、插件 digest 变化、symlink / reparse、dirty worktree、并行 writer 冲突 |

完成条件不是“测试数量增加”，而是每项用户可见承诺都有一条可复现证据。

## 19. 采用 / 改造 / 拒绝清单

| 上游模式 | 决定 | 理由 |
| --- | --- | --- |
| Amp Command Palette | 采用 | 与现有 CommandRegistry 天然一致 |
| Amp queue / steer / interrupt | 已采用并继续 | Chaos4 已有事件证明，优于纯 UI 状态 |
| Amp 默认不审批 | 拒绝 | 不符合 Windows 本地安全与 typed policy |
| Codex sandbox / approval 分层 | 采用概念，分步实现 | Chaos4 目前缺 OS sandbox，但可先统一状态模型 |
| Codex 完整 working tree diff | 采用 | 防止漏审 untracked |
| Claude Transcript Viewer | 改造 | 用正常 scrollback + Inspector，不默认 fullscreen |
| Claude checkpoint / rewind | 采用只读部分 | 任意 checkpoint 的 conversation / code / both preview 已交付；apply 未采用 |
| Claude 全功能后台 / cloud | 暂缓 | 需要新的运行与威胁模型 |
| uv-agent 单 run_python | 拒绝替换 typed tools | 可审计不等于最小权限 |
| uv-agent 渐进上下文 | 采用 | 与现有 Skill / MCP / Rule 分层一致 |
| uv-agent NetGain compaction | 试验 | 先接真实 thread_id，再 A/B |
| CodeWhale side-git restore | 仅采用外置 snapshot 与验证链 | 未采用 restore；只有 exact preimage / continuity / current-tip 全部通过才声明保存 baseline |
| CodeWhale Plan / Agent / YOLO | 只采用轴分离 | 不采用 YOLO 作为日常模式 |
| CodeWhale Fleet / daemon | 暂缓 | 当前单前台任务是明确契约 |

## 20. 风险与反方审查

最强反方意见是：“既然 Codex / Claude / CodeWhale 已经实现这么多，Chaos4 直接复制功能面即可。”

这不成立，原因是：

- Chaos4 的明确优势是 Windows 正常终端行为、typed tools、目录契约和任务证据；复制 fullscreen / daemon 会损害这些优势。
- 上游很多能力依赖云账户、OS 沙箱、独立 Rust / Node Runtime 或大型团队维护，不能从 UI 截图推导可移植性。
- 功能面越大，命令、键位、配置、权限和恢复的组合状态越多。uv-agent 的同发布契约漂移已经说明“快速加功能”会损害可信度。
- CodeWhale 和 Claude 的高自治模式都需要额外 classifier、protected paths、snapshot、worktree 或云隔离；只复制一个“YOLO”按钮最危险。
- Chaos4 的真实身份、checkpoint / mutation ordering 与只读 preview 已接通；当前刻意边界是无破坏性 apply / restore，而不是再写一套摘要 UI。

因此本报告把“恢复能力、审阅能力、可发现性、证据链”排在“更多并行、更多远程、更多自动发布”之前。

## 21. 局限与复现说明

- 未安装并实机操作五个上游 CLI；外部结论来自官方文档、固定提交源码、发布记录和官方截图。
- Amp 主 CLI 源码未见官方公开，完整动态命令表只能由已安装的 <code>amp --help</code>、<code>amp config keymap</code> 和 <code>amp tools list</code> 获取。
- Claude fullscreen 明确属于预览；Codex、Claude、Amp 的命令和 model routing 会滚动变化。
- uv-agent 的 CLI / 键位漂移结论绑定 v0.21.4 固定提交，不代表未来提交没有修复。
- CodeWhale 的 main 分支 README 与架构文档在访问时存在相邻版本号差异，因此报告只依赖稳定设计事实，并使用固定提交做身份和截图证据。
- 官方图片是视觉参考，可能滞后于当前 CLI；本报告没有做用户实验、性能基准或无障碍审计。
- Chaos4 工作树在 2026-07-16 初稿研究前已脏；初稿没有修改源码或重跑 554 项历史快照。后续 P0 recovery 是独立实施切片，Feature 共运行 530 项，其中 526 项通过、4 项平台 skip；root integration 163 项全部通过。它没有被冒充为已发布 1.0.2。

复现方式：按下方来源链接访问官方材料，并在目标发布版本运行各产品自己的 <code>--help</code>、keymap / commands、tools、permissions 和 version 命令，再与本报告快照比较。

## 22. AI 使用与责任披露

本报告由 OpenAI Codex 在 2026-07-16 协助完成，使用三个并行子 Agent 做来源筛选与初步归纳，主 Agent 负责复核、Chaos4 本地审计、冲突排查、反方审查和最终写作。未收集人类受试者数据，未访问用户私有云服务。AI 可能遗漏滚动发布变化；在进入实现阶段前，项目负责人仍应确认目标版本、范围和验收标准。

## 23. 官方来源

### Amp

- [Amp Owner’s Manual][AMP-MANUAL]
- [Amp CLI 与 Keybindings][AMP-CLI]
- [Command Palette, Not Slash Commands][AMP-PALETTE]
- [Tools / Subagents][AMP-TOOLS]
- [Permissions][AMP-PERMISSIONS]
- [Plugins][AMP-PLUGINS]
- [Amp, Rebuilt][AMP-REBUILT]
- [The Dial / mode][AMP-MODE]

### Codex

- [Codex CLI][CODEX-CLI]
- [Developer Commands][CODEX-COMMANDS]
- [CLI Customization][CODEX-CUSTOM]
- [Agent approvals & security][CODEX-SECURITY]
- [Sandbox][CODEX-SANDBOX]
- [AGENTS.md][CODEX-AGENTS]
- [Non-interactive mode][CODEX-EXEC]
- [MCP][CODEX-MCP]
- [Skills][CODEX-SKILLS]
- [Subagents][CODEX-SUBAGENTS]
- [Hooks][CODEX-HOOKS]
- [官方仓库与 splash][CODEX-REPO]

### Claude Code

- [Interactive mode][CLAUDE-INTERACTIVE]
- [Commands][CLAUDE-COMMANDS]
- [Permission modes][CLAUDE-MODES]
- [Permissions][CLAUDE-PERMISSIONS]
- [Checkpointing][CLAUDE-CHECKPOINT]
- [Fullscreen][CLAUDE-FULLSCREEN]
- [Settings][CLAUDE-SETTINGS]
- [Memory / CLAUDE.md][CLAUDE-MEMORY]
- [Hooks][CLAUDE-HOOKS]
- [官方仓库 demo][CLAUDE-REPO]

### uv-agent

- [v0.21.4 README 固定提交][UV-README]
- [CLI 源码][UV-CLI-SOURCE]
- [按键源码][UV-KEY-SOURCE]
- [TUI 文档][UV-TUI]
- [Runtime][UV-RUNTIME]
- [Plugins][UV-PLUGINS]
- [官方截图][UV-SCREENSHOT]

### CodeWhale

- [README][CW-README]
- [Rebrand][CW-REBRAND]
- [v0.8.41 Release][CW-RELEASE]
- [Guide][CW-GUIDE]
- [Modes][CW-MODES]
- [Tool Surface][CW-TOOLS]
- [Architecture][CW-ARCH]
- [固定提交截图][CW-SCREENSHOT]

[AMP-MANUAL]: https://ampcode.com/manual
[AMP-CLI]: https://ampcode.com/manual#cli
[AMP-PALETTE]: https://ampcode.com/news/command-palette
[AMP-TOOLS]: https://ampcode.com/manual#tools
[AMP-PERMISSIONS]: https://ampcode.com/manual#permissions
[AMP-PLUGINS]: https://ampcode.com/manual#plugins
[AMP-REBUILT]: https://ampcode.com/news/neo
[AMP-MODE]: https://ampcode.com/news/the-dial

[CODEX-CLI]: https://learn.chatgpt.com/docs/codex/cli
[CODEX-COMMANDS]: https://learn.chatgpt.com/docs/developer-commands?surface=cli
[CODEX-CUSTOM]: https://learn.chatgpt.com/docs/cli-customization
[CODEX-SECURITY]: https://learn.chatgpt.com/docs/agent-approvals-security
[CODEX-SANDBOX]: https://learn.chatgpt.com/docs/sandboxing
[CODEX-AGENTS]: https://learn.chatgpt.com/docs/agent-configuration/agents-md
[CODEX-EXEC]: https://learn.chatgpt.com/docs/non-interactive-mode
[CODEX-MCP]: https://learn.chatgpt.com/docs/extend/mcp
[CODEX-SKILLS]: https://learn.chatgpt.com/docs/build-skills
[CODEX-SUBAGENTS]: https://learn.chatgpt.com/docs/agent-configuration/subagents
[CODEX-HOOKS]: https://learn.chatgpt.com/docs/hooks
[CODEX-REPO]: https://github.com/openai/codex

[CLAUDE-INTERACTIVE]: https://code.claude.com/docs/en/interactive-mode
[CLAUDE-COMMANDS]: https://code.claude.com/docs/en/commands
[CLAUDE-MODES]: https://code.claude.com/docs/en/permission-modes
[CLAUDE-PERMISSIONS]: https://code.claude.com/docs/en/permissions
[CLAUDE-CHECKPOINT]: https://code.claude.com/docs/en/checkpointing
[CLAUDE-FULLSCREEN]: https://code.claude.com/docs/en/fullscreen
[CLAUDE-SETTINGS]: https://code.claude.com/docs/en/settings
[CLAUDE-MEMORY]: https://code.claude.com/docs/en/memory
[CLAUDE-HOOKS]: https://code.claude.com/docs/en/hooks
[CLAUDE-REPO]: https://github.com/anthropics/claude-code

[UV-README]: https://github.com/uv-agent/uv-agent/blob/3567abecbcd5bd85283d526d9dac4f1f1f081c8b/README.md
[UV-CLI-SOURCE]: https://github.com/uv-agent/uv-agent/blob/3567abecbcd5bd85283d526d9dac4f1f1f081c8b/src/uv_agent/cli.py#L11-L91
[UV-KEY-SOURCE]: https://github.com/uv-agent/uv-agent/blob/3567abecbcd5bd85283d526d9dac4f1f1f081c8b/src/uv_agent/tui/app.py#L844-L909
[UV-TUI]: https://github.com/uv-agent/uv-agent/blob/3567abecbcd5bd85283d526d9dac4f1f1f081c8b/docs/tui.md
[UV-RUNTIME]: https://github.com/uv-agent/uv-agent/blob/3567abecbcd5bd85283d526d9dac4f1f1f081c8b/docs/runtime.md
[UV-PLUGINS]: https://github.com/uv-agent/uv-agent/blob/3567abecbcd5bd85283d526d9dac4f1f1f081c8b/docs/plugins.md
[UV-SCREENSHOT]: https://raw.githubusercontent.com/uv-agent/uv-agent/3567abecbcd5bd85283d526d9dac4f1f1f081c8b/docs/t2.png

[CW-README]: https://github.com/Hmbown/CodeWhale
[CW-REBRAND]: https://github.com/Hmbown/CodeWhale/blob/main/docs/REBRAND.md
[CW-RELEASE]: https://github.com/Hmbown/CodeWhale/releases/tag/v0.8.41
[CW-GUIDE]: https://github.com/Hmbown/CodeWhale/blob/main/docs/GUIDE.md
[CW-MODES]: https://github.com/Hmbown/CodeWhale/blob/main/docs/MODES.md
[CW-TOOLS]: https://github.com/Hmbown/CodeWhale/blob/main/docs/TOOL_SURFACE.md
[CW-ARCH]: https://github.com/Hmbown/CodeWhale/blob/main/docs/ARCHITECTURE.md
[CW-SCREENSHOT]: https://raw.githubusercontent.com/Hmbown/CodeWhale/9035890c464b1815bc592fc5f4b00d0fe7837ffb/assets/screenshot.png
[WHALE-OTHER]: https://github.com/usewhale/Whale
