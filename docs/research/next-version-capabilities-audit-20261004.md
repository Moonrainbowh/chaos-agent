# 下一版本能力与上下文审计（2026-10-04）

## 判断

这一部分最值得投入的是把已有能力接成一个可靠、可解释的产品，而不是再增加 Memory 框架、向量库、Agent 协议或插件执行层。已有 SQLite 持久事实、共享仓库索引、typed action、受限扩展目录值得保留；主要问题是**不同上下文路线、不同历史检索接口和未接通的长期记忆同时存在，功能名相似却覆盖范围不同**。

建议下一版本首先落实：一个生产上下文入口、一个可追溯历史读取服务、可实际维护和检索的项目记忆、可诊断且有完整生命周期边界的 MCP。Skills 保持指令层，MCP 保持外部工具接入层，Plugins 收敛为声明式包装与分发，Tools 继续是实际执行契约。不要把五者合并成一个万能执行框架。

审计基于当前工作区 HEAD `5793c72` 及当时文件内容；没有改实现、私人配置或真实对话数据库，没有调用模型或外部服务。本文件是本子任务唯一仓库产物。父审计仍需综合 runtime、产品入口、发布与验收证据。

## 1. 项目记忆具备存储基础，但没有生产使用闭环

**高置信度接线缺口。** `RuntimeContextFactory._strategy_context` 进入 managed 路径时，只传入旧参数，没有传 `memory_project_id`（`chaos_agent/application_context.py:105-109`）。`build_managed_context` 的作用域参数默认都是关闭态，并明确说明既有调用不会得到 memory projection（`chaos_agent/managed_context.py:15-36`）。`PersistentContextBuilder._load_memory_context` 没有 project ID 就立即返回空（`src/code_agent/context_windows/persistent_builder.py:107-115`）。在 `chaos_agent` 和界面源码中没有找到生产的 `create_memory`、`revise_memory`、`promote_memory` 操作入口。

影响：不能把当前产品描述成已经会跨任务记住项目经验；目前确实可用的是 task Notes/History，项目与用户记忆主要是库级基础设施。这里没有读取用户实际库，因此不推断用户现有数据多少。

**即使补上接线，现有查询也会漏掉相关老记忆。** `search_memories` 对每个授权 scope 先 `ORDER BY updated_at DESC LIMIT ?`，再在 Python 中做匹配（`src/code_agent/sessions/_memory.py:99-112`）。生产投影给的 limit 是 4。因此四条更新但不相关的记录就能把唯一相关旧记录挡在检索之外。中文自然语言还只是空格分词加子串匹配（`:106`），没有代码索引中的中文词项能力。诊断接口另用 `limit * 2` 的候选范围，不能当作实际注入结果的精确解释（`:115-131`）。

离线反例：合成 5 条 active project memory，最旧一条包含 `sqlite`，其余 4 条不含；`limit=4` 返回 0，`limit=5` 返回 1，已经实际复现。

**保留** scope、revision、来源、条件、撤回和遗忘保护（`_memory.py:34-60,62-78,89-97,134-157`）；**补齐**可信 project identity → 查看/新增/修订/撤回入口 → 先匹配后排序/截断 → 实际注入来源与排除理由。第一版只做明确保存的项目决策；不急于自动提炼全部聊天、跨项目迁移或 embedding。

## 2. 上下文策略应统一产品语义，保留原始历史与预算边界

当前存在四条策略状态：没有 context policy 的旧 semantic pipeline，以及 managed `summary`、`boundary`、`persistent`。没有配置/metadata enable 时仍返回 `None`（`src/code_agent/config/context_policy.py:5-27`）；生产据此选择不同构建链（`chaos_agent/application_context.py:105-129`）。`WindowPolicy` 自身默认又是 `boundary`（`src/code_agent/context_windows/policy.py:6-19`）。

它们不是纯粹无用的重复代码：旧链使用 semantic checkpoints；boundary/summary 使用 HandoffWriter；persistent 明确不生成交接摘要、按需读 Notes。问题在于用户必须理解多个实现策略，而恢复、计量和工具能力又随策略改变。`docs/context-boundary-results.md:3` 留存的既有实验也没有证明 boundary 提升效率，报告总 token 增加 1.77%；本轮未复跑该历史实验，不能推广到当前默认或多窗复杂任务。

建议保留一个完整生产上下文组装与最终计数入口；将“摘要还是 Notes”变成同一历史事实/预算接口下的策略。先用真实任务续接验收选择默认，再把其他策略移到明确的实验/兼容入口，不直接删除旧会话读取能力，不先凭直觉把 persistent 全局开启。

有一处值得在统一过程中优先补齐：`BoundSkillContextBuilder` 在底层已分配预算后追加完整 Skill（`chaos_agent/runtime_extensions.py:142-149`），`with_system_prompt` 只更新数字，不执行容量检查（`src/code_agent/context/measurements.py:13-20`）。managed 外层会再完整计数；旧 semantic 路径没有同样的最终窗口容量门。因此不能把局部 `prompt_budget_tokens` 当成完整 system/tool/message 请求的统一硬约束。

## 3. 历史检索有两套覆盖语义，且“输出分页”没有约束读取成本

`search_threads/read_thread` 通过 `thread_index_entries` 工作（`src/code_agent/thread_intelligence/tools.py:34-73`；`src/code_agent/sessions/_semantic.py:61-125`），这个表在 `publish_semantic_checkpoint` 时写入（`_semantic.py:26-47,129-169`）。`ThreadAwareContextBuilder` 只在获得新 checkpoint 时发布 entries（`src/code_agent/thread_intelligence/context_builder.py:91-112`）。因此普通新消息并不立即可搜；persistent 不走这条发布路径。

离线反例：新建合成 thread，append 1 条包含 `sqlite` 的 user message，durable message 数为 1，但 `search_thread_index(..., 'sqlite')` 返回 0。现有同名搜索是 checkpoint 索引搜索，不能作为全量历史搜索的替代。

另一组 `history_*` 直接读原始消息，能够覆盖工具参数和结果，但只做大小写敏感字面匹配（`src/code_agent/context_windows/persistent_history.py:48-65,80-100`）；`notes_*` 又是独立的虚拟文件搜索。范围有合理区别：跨父子 thread 的授权查询不能直接与本 task Notes 混为一谈；需要统一的是 source anchor、分页、检索覆盖说明与底层读取接口，不是抹掉权限。

长任务成本还未真正有界：persistent 每轮读出整个 thread 的 records，并逐个历史窗口校验 source（`persistent_builder.py:38-50`）；`history_read_item` 也先全量 load records 再找 item（`persistent_history.py:48-62`）；`read_thread` 读取全部授权 entries 再建内存索引（`thread_intelligence/tools.py:57-73`）。`select_window` 每次扫描 records 并重新序列化 source digest（`context_windows/history.py:8-10,33-39`）。页大小和返回字符数有界，不代表数据库 I/O、JSON 解码和校验成本有界。本轮未做大规模性能测量，所以不报实际延迟数字。

建议复用现有 SQLite journal 建立 sequence/cursor 范围读取、按 stable ID 定位、scope-aware 搜索；raw history 的索引更新与摘要生成解耦。验收包含短 thread 可搜、旧消息更正、工具参数检索、多窗重启、1万/10万消息读取成本随页大小而不是全部历史增长。

## 4. 共享仓库索引是值得保留的设计；语义报告应保持静态候选定位

`RepoIndexSnapshot` 从同代 entries 构造共享图（`src/code_agent/context/repo_index.py:27-49`），无 dirty/reconcile 时返回同一快照（`:90-99`），FTS 与 generation 在统一更新边界发布（`:126-158`）。TUI `SemanticGraphControl` 从当前 thread workspace 获取相同 `repo_index`，后台刷新后消费它（`chaos_agent/semantic_insights.py:31-68`）。这些比再建一个向量库/全仓扫描服务更有价值。

它不是完整跨语言代码理解器：`rank_bug_locations` 是词项与依赖邻接加权（`src/code_agent/semantic_insights/ranking.py:38-57,92-120`）；dead-code 是无静态消费者模块或未解析到引用的私有符号（`:158-210`）。代码正确标注 `heuristic/candidate`。因此不要因命名为 Semantic Insights 就把它当自动定位根因、自动删代码或安全重构的证明。

建议保留 Context/Verification 共用的一图和 L0/L1/L2 预算；优先验收最终 Prompt 是否真的包含目标源码、源码变更后是否刷新、错误候选是否有明确依据。将低频 risk/review/refactor/dead-code 视图保留为可选只读诊断，暂停继续扩展示图数量；没有用量或实际收益证据前，不凭“复杂”直接删底座。

## 5. Tools 的权限与发现分离正确，但五个名字不等于五个简单 schema

`RestrictedDispatcher` 先过滤原工具，再生成 compact definitions，最终展开回原 action（`chaos_agent/restricted_dispatcher.py:68-78,108-116`）；capability loader 只改变模型下轮 schema 披露，schema digest 变化失效（`src/code_agent/capabilities/catalog.py:42-84,87-122`）。这条边界值得保留，不需要 lease、另一个权限目录或万能 `execute` URI。

`compact_definitions` 把各 operation 的字段合并到一个 schema，只强制 `operation`，各分支必填项写在 description（`src/code_agent/capabilities/compact_tools.py:28-40`）；`expand_request` 才校验分支 missing/unexpected fields（`:44-61`）。这样减少工具名数量，但仍保留完整操作描述与字段，且让合法参数组合较依赖模型阅读说明。不能仅用工具数量宣称 token 或调用正确率一定改善。

建议保留 hybrid 为当前合理起点；用相同任务对照统计最终 schema tokens、首次有效操作前回合数、参数修复率。只有结果支持才删除 `legacy/progressive` 兼容实验项，或拆开误用严重的 operation。不要把“所有能力仅留两个工具”当目标。核心验收是更少失败/探索回合与相同权限边界。

## 6. Skills 保持指令，不扩成流程引擎；改善按任务适用性与预算

当前 `.agents/skills` 有界读取、来源/digest 冲突隔离、thread 激活身份恢复是合理基础（`src/code_agent/skills/registry.py:35-59,66-105`；`controller.py:139-173`）。Skill 不自己注册工具、不扩大 policy，这个分工应保留。

待改之处：

- 每次请求渲染该 thread 全部 active Skill 全文，限制是 64000 字符（`registry.py:65-80`；`controller.py:45-58`）。长会话切换主题后会继续承担旧 Skill 成本；不应把所有技能默认注入。
- 自动匹配当前接在 TUI 提交路径（`src/code_agent/interfaces/tui_run.py:66-87`）；不能把它当 Host-neutral 的所有 CLI/ACP 能力。需要一致的服务入口和可见的激活原因。
- `requires` 只是当前 ToolDefinition 名称匹配（`src/code_agent/skills/capabilities.py:41-55`），容易与 compact/底层工具名及 MCP namespace 耦合。优先以一个稳定操作 ID 映射解释缺失能力，而不是再创建复杂能力框架。
- `enable_many` 对缺能力做了整组预检，但批准/激活/落库逐个发生（`controller.py:109-137`）；第二项被拒绝或写入失败时第一项可能已生效。应把组合能力闸门与组合激活的原子性区分开来；本轮这是源码判断，未专门构造该失败反例。

建议只新增“本次任务/持续激活”的明确生命周期与最终预算纳入，保留显式调用及高置信度建议。暂不升级到 Skill DAG、自动 Agent 工厂或低频自动删除。

## 7. MCP 真正需要的是可诊断的接入与恢复，不是更多协议包装

当前官方 SDK adapter + typed policy bridge 方向合理。已授权 stdio 配置提供进程 argv/environment allowlist，工具须有本地风险映射，未知工具不暴露（`src/code_agent/mcp/registry.py:31-87`；`official_sdk.py:17-45`）。保留这个边界。

具体缺口：

- `start_timeout_s` 只包 `list_tools`，`adapter.start` 不在该超时里（`stdio_manager.py:34-48`）；SDK initialize 正在 `adapter.start` 中（`official_sdk.py:25-32`）。合成 adapter 延迟 31ms，在 manager 配置 1ms 时仍成功，已复现。这证明项目声明的 start timeout 没覆盖完整启动，不等于已经证明真实 SDK 无限等待。
- close/cancel 没有本层 deadline；health 只检查 adapter 是否在字典（`stdio_manager.py:55-72`）。工具 timeout 后没有明确清理/标记 unhealthy/reconnect 状态；不宜称有完整故障恢复。
- 配置 enabled/approved 与实际已启动工具目录是不同状态。`Application.startup` 没有启动 MCP（`chaos_agent/application_model.py:55-67`），显式 `/mcp enable` 才调用握手；`/mcp status` 只展示 enabled/disabled（`interfaces/tui_mcp_commands.py:14-35`）。所以“enabled”不足以说明可用，应明确 configured/starting/ready/failed。
- 命令捕获常见异常后只输出异常类型（`tui_mcp_commands.py:38-40`），用户很难分清未批准、缺 SDK、进程退出、风险映射为空、超时。配置仍要求逐 tool 风险映射，不是即插即用。

优先补一个薄的诊断/配置预检入口、生命周期 owner 与完整 deadline、已知失败类别及恢复动作。只重试已知未执行或明确只读幂等请求；超时 mutation 必须保留结果未知。HTTP transport/OAuth 仅在明确所需的实际 server 上再扩展，本轮没有网络兼容性结论。

## 8. Plugins 的安全边界可保留，贡献面应冻结或缩小

Plugins 已提供 tools、commands、modes、custom agents、events、UI proposals；它不是缺一个任意代码执行回调。工具只能映射已知 Host/MCP，事件提案再次走中央 policy（`chaos_agent/plugin_runtime.py:25-43,148-188`）。generation/digest-bound snapshot 和撤销控制是有用机制。

产品接入却偏手工：manifest SHA-256 需匹配独立 `plugin-trust.json`（`plugin_runtime.py:238-250,275-289`；`src/code_agent/plugins/manifest.py:73-80`）。当前 Host 控制器只能 enable/disable/reload 已加载可信快照，不提供完整安装/可信审查流程。Skills、MCP、Plugin 各有来源、状态、信任和入口，用户要理解三个独立管理面。

建议把 Plugin 定义收敛为“同一来源的一组 Skill、MCP 配置和可选命令元数据”的包装/分发层，内部仍调用现有三个服务，不建第四套执行/记忆机制。对于 events/custom agents/custom modes，先冻结新增，记录实际使用场景与维护成本；有真实依赖就保留兼容，不能因本轮未读私人插件目录便断言无人使用。下一版本不建议开发插件市场、任意 Python/shell 插件或更多声明式编排语法。

## 9. Web 能力仍是最小原型，需要定位清楚

`WebAccessService.search` 依赖 DuckDuckGo HTML 固定结构正则（`src/code_agent/web_access/service.py:39-62`）；fetch 返回有界原始 HTML（`:64-72`），site API 仅 GitHub repo/arXiv（`:74-87`）；browser_fetch 每次新建无登录持久化的 headless Chromium（`:115-132`）。`retrieve` 只串接 API/fetch/search，不自动调 MCP/browser（`:89-113`）。因此“分层访问”是产品路由约定，尚不是一个会自动选可靠来源的完整浏览器/搜索系统。

保留有界 HTTP 和来源 URL；让成熟 MCP/现有浏览器服务承担登录态和复杂站点。先做好可读文本、明确 HTTP/挑战页错误、搜索候选与已读取正文的区分；无具体用例不维护另一套账号/Cookie/browser session 系统，也不增加大量站点专用 adapter。本轮未发真实搜索请求，不能声称当前搜索供应商可用或不可用。

## 10. 验收覆盖有一个必须先解决的缺口

`scripts/run_test_suite.py:125-131` 使用 unittest discovery；`src/code_agent/sessions/tests/test_memory.py:11` 是模块级 pytest 风格函数，依赖 `tmp_path`。当前 `.venv` 无 pytest。实际运行 `python -m unittest discover -s src/code_agent/sessions/tests -p test_memory.py -v` 返回 **Ran 0 tests / OK**。所以常规全量回归全绿不能作为该 Memory 测试执行过的证据。应统一测试入口，或增加“发现预期测试数量与执行数量一致”的基本检查。

本轮离线验证：

| 验证 | 结果 | 边界 |
|---|---:|---|
| capabilities unittest | 12 通过 | 纯投影/展开 |
| skills unittest | 25 通过 | 临时 Skill 与临时 SQLite |
| context_windows unittest | 24 通过 | mock provider / 合成历史 |
| persistent + managed runtime integration unittest | 3 通过 | `tests.test_persistent_runtime`、`tests.test_managed_context_runtime` |
| memory/search/MCP 启动小型反例 | 三项复现 | 全部合成输入、临时数据库、fake adapter、0 网络 |
| memory 的标准 unittest discovery | 0 项 | 已确认测试入口覆盖缺口 |

合计 64 项现有 unittest 通过，不是全仓回归，也不是完整产品长期运行、模型收益或真实 MCP 接入验收。临时反例脚本与数据库已清理，原有未提交文件未修改。

## 建议迭代顺序与退出条件

1. **P0：先让验证和状态可信。** 修复漏测入口；明确 raw history 与 semantic index 覆盖；MCP 完整启动/关闭边界和错误状态；最终 prompt 统一预算计数。退出条件是上述反例有自动回归，不再以 0 tests/配置 enabled/局部预算充当成功。
2. **P1：一个可连续工作的上下文系统。** 保留 durable messages、完整工具组、Notes、task facts、sources；统一组装/计量/分页/读取。用短任务、跨多窗、进程重启、失败后接续的固定样本选默认策略，旧策略只留兼容/实验入口。避免先删模块再找需求。
3. **P1：最小可用项目记忆。** 绑定明确 project identity，用户可见保存/查看/修订/撤回，先修匹配顺序与来源解释，再开默认读取；确认一条旧但相关的决策能被恢复、被新决策替代、被撤回后不再注入。用户级记忆继续显式选择。
4. **P2：减轻扩展使用负担。** 一处能力清单说明来源、可用性、缺失依赖、启用动作；Plugin 作为包装，Skills/MCP 服务继续各司其职。用两个真实扩展从安装到调用验收，不能仅验证 manifest parse。
5. **P2：凭任务数据做减法。** 比较 hybrid/compact tools 对 schema 成本与误用率的实际影响；有迁移方案后收窄旧策略、重复指令入口和没有依赖的 Plugin 高级贡献面。语义图保留共享底座，暂停增加静态报告品类。

不建议本版本投入：另建向量记忆数据库、全仓常驻 daemon、自动把全部会话提炼为记忆、通用插件执行沙箱、Skill 工作流语言、无约束自动重试、为功能数量增加更多内置网站 adapter。
