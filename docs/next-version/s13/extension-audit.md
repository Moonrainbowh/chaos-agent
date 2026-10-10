# S13 扩展边界与五入口只读审计

结论：保留五个紧凑入口，保留现有 Skill/Plugin 的受限包装能力；本阶段优先修复 MCP 生命周期、工具快照与执行前的当前定义绑定。没有证据支持删除未知用户插件或新增执行机制。审计输入为主源码在 S13 开始时的状态，后续实现应重新核验变更。

## 真实调用链

### Skills

`Application.skills = SkillController(root, sessions, SkillApprovalAdapter)` → `SkillRegistry.discover` → user `.agents/skills`（trusted）及 workspace `.agents/skills`（显式批准）→ thread activation → `SkillContextBuilder.build` 追加指令并同步本地 prompt token 度量 → 统一 model budget。Skill 不注册工具，不执行脚本，不授网络权。

- `SkillRegistry` 读取 UTF-8、拒 symlink、单文档 64 KiB，按 ID/digest 合并或冲突隔离。
- `SkillActivation` 为激活文本设 64,000 字符上限；字符限额与总 Prompt token 限额是两层限制，不可互相替代。
- `SkillController.restore/reload` 校验持久身份的 source/digest；漂移移除记录。`reload` 清进程内 activation；指定 thread 时恢复该 thread。普通 `render` 使用已加载文本，不能表述成每次请求重新读磁盘。
- capability gate 比较只读 ToolDefinition 快照，缺少任意需求时 `enable_many` 整组在审批/持久化前拒绝；通过不替代 policy。
- TUI 显式 `/<skill>+<skill>` 路径使用 `tui_run._enable_skills`，逐个 enable，异常/取消回滚本次新增及正在尝试的项，保留先前激活；并非直接调用 `enable_many`。

### Plugins

`compose_host._plugin_discovery` → `load_plugins` → manifest/trust store → `PluginRegistryBuilder` → immutable `ContributionSnapshot` → `PluginHost` → `PluginToolBridge` / CommandCatalog / EventCoordinator。Manifest 目录为 `%LOCALAPPDATA%/chaos-agent/plugins` 与 workspace `.chaos-agent/plugins`；本审计没有删除、迁移或声称完成未知外部依赖盘点。

- 文件有界 256 KiB，JSON schema/namespace/host_api/digest 校验；信任以 ID+digest 绑定，未信任项不活跃。
- 工具只映射 Host typed action 或配置的 MCP target；禁止 shell/Python/URL 动态执行贡献。
- Registry 检查内建 namespace/ID 冲突；Host 已知动作的风险不得降低；Plugin MCP target 还必须经过当前 target 的中央 policy。
- `PluginCommandController.reload` 在活动任务期间 stage，Foreground settled 后 apply；启停要求 idle。`PluginHost.disable/revoke` 本身立即推进 generation 并撤下贡献。直接 Host revoke 与 UI idle-only disable 应准确区分。
- 成功 apply/enable/disable → `refresh_plugin_surfaces` → `PluginRuntimeBindings.refresh` 更新中央风险及绑定 RestrictedDispatcher allowlist，再更新命令/模式投影。已存在命令/事件 invocation 有 generation/digest 校验。
- Plugin 工具来自当前 bridge，仍以 qualified plugin 名进 Root dispatcher；Root 同时检查源 Plugin 与翻译 target 的 policy，不能把监督用途的目标映射当实际调度绕过。

### 当前工具快照、Schema 与中央权限

`RootActionDispatcher.tools` 合并内建/threads/peers/MCP/plugin/managed → Mode/Role RestrictedDispatcher 先过滤 → `compact_definitions` → `progressive_tools` 根据当前完整定义 digest 决定下一轮披露。

调用 `read/search/write/edit/execute` → `expand_request` 校验 operation、模式允许的目标、缺失/无关字段，保 action ID → Root `preflight_action` → 内建递归 Schema/特定兼容检查 → 中央源/目标 policy、审批 → workspace/runtime 或 MCP SDK。

在审计时源码中，`preflight_action` 的 `validate_tool_arguments` 仅查内建工具字典，未知名返回 None；peer 有单独 validator。因此：

1. plugin→builtin 检查 builtin target Schema。
2. Plugin 自声明 `input_schema` 只作为工具定义发布、JSON mapping 校验；没有按该 Schema 的本地参数匹配。
3. direct MCP 与 plugin→MCP 没有 Host 本地 `inputSchema` 匹配，参数由 manager 转发 `OfficialMcpSdkAdapter.call` → session `call_tool`；服务端是否拒绝不在本审计结论内。

这属于 S13 执行前定义/Schema 一致性待修复项，不构成“已绕过中央 policy”的证据。审批 await 期间 Plugin/MCP generation、目标映射、Schema/risk 若变化，需执行前重核，避免旧批准覆盖新定义；主 Agent 负责相应修复。

## 离线五入口对照

执行脚本 `extension_probe.py`，锁定候选 `.venv/Scripts/python.exe`，源码显式取 main。手工固定 13 个规范操作，调用真实 `expand_request` + `validate_tool_arguments`，没有实际运行 shell、写文件、网络、Provider 或用户 DB。

成本方法：相同 ToolDefinition 的 `to_dict()`，规范 JSON `sort_keys=True, separators=(",", ":"), ensure_ascii=False`；估算采用项目 `estimate_tokens`。这些不是 Provider 的实际计费 token；比较只覆盖五常用入口对应的 13 个底层工具，不包含 loader、web、MCP/plugin 长尾。

| 入口 | 底层操作数 | 底层 JSON bytes /估算 token | 紧凑 JSON bytes /估算 token | 固定有效/紧凑错误/可比底层错误 |
|---|---:|---:|---:|---:|
| read | 2 | 1452 / 363 | 1458 / 365 | 2 / 10 / 6 |
| search | 2 | 1124 / 281 | 1102 / 276 | 2 / 8 / 4 |
| write | 1 | 365 / 92 | 475 / 119 | 1 / 5 / 3 |
| edit | 3 | 1592 / 398 | 1470 / 368 | 3 / 15 / 9 |
| execute | 5 | 2297 / 575 | 1972 / 493 | 5 / 21 / 11 |
| 合计（整组序列化） | 13 | 6826 / 1707 | 6473 / 1619 | 13 / 59 / 33 |

规范样例有效率：底层 13/13、紧凑 13/13，action ID 均保留。紧凑 59 个固定错误全部拒绝；可比的底层 33 个错误也全部拒绝。紧凑额外错误为 missing/unknown operation 共 26 个。每个错误使用预设正确参数替换后 1 次恢复；这是脚本纠正轮数，不是模型自纠轮数或模型参数正确率。

总估计 token 减少 88（约 5.2%），部分入口反而增长：write 增加 discriminator，read 几乎持平。五个名称并不等于可靠性收益。当前 schema 合并只将 operation 写入 `required`，各操作必需字段通过描述与运行时检查表达；运行时能拒绝缺参，不代表 provider schema 对缺参提前失败。没有真实模型 A/B 证据，不能以离线样例宣称调用更可靠。决定：保持接口，暂不拆分；后续只有可冻结、同权限、同底层执行的模型对照显示明确收益时再评估拆分。

## 可复现缺口与级别

### 必须在 S13 核验

- MCP server 失效应同步撤下 definitions 与本地风险，状态不可只凭连接表或静态 tools 判断；审批中 generation/schema/risk 漂移后旧调用必须失败闭合。
- MCP / plugin-MCP 参数 Schema 目前不做 Host 本地匹配；补安全且有界的当前定义校验，保留官方 SDK 作为传输边界，不自写协议。
- 真实 SDK 启动/初始化/list/call/关闭的故障注入及同任务生命周期所有权，由 MCP 实现与独立监督验证；本审计没有运行真实 MCP SDK，不能替代该门槛。

### 非阻断台账：公开 SkillController 的组合失败语义

`extension-probe.json` 的内存 Store/Approval 固定 probe：`enable_many(first, second)`，first 审批通过，second 拒绝 → 抛 PermissionError，但 first 仍 active，且已保存一条 activation。此 probe 不接 SQLite，不是生产数据库写入事实；它确定方法已产生部分激活和一次 save 调用。

Feature 只承诺“能力缺失整组拒绝”，没有承诺审批/预算/持久失败全有全无；生产组合 TUI 具有额外 rollback，因此不能把这个 Controller probe 上升为手机/TUI现存错误。建议保留台账，未来统一组合入口时先明确契约，再加入第二项拒绝、组合预算耗尽、持久保存失败及既有激活保留测试。当前不扩围修改 Skill。

独立单项 Skill budget probe：第二个指令导致 50-char 限额超限时抛 ValueError，第一项保留，第二项未残留，符合有界注入。

## 保留与冻结清单

- 保留 Skill 本地方法/指令定位、显式 task/thread 激活、能力只读闸门、持久身份、字符/Prompt 双预算；不加脚本执行权或自动记忆 Agent。
- 保留 Plugin manifest/trust、namespace、工具/命令包装、不可变 snapshot、generation 及中央源/目标双检查；不改变 typed action 的执行通道。
- 现有事件、模式、自定义 Agent 与 UI primitives 保留兼容及测试；冻结高级事件、角色、模式扩张，不新增调度器、插件市场或通用能力加载权限。
- 保留官方 MCP SDK，本地风险以 operator 映射为准；不相信 server 自报低风险，不自动重放未知写入，不因重连获得新权限。
- 不删除未知用户插件或以“仓库没有样例”断言无人依赖；S15 删除必须另行证据盘点。

## 已执行测试与局限

- Skills 25 tests / 0.714s OK，`extension-skills-tests.log`。
- Plugins 22 tests / 0.013s OK，`extension-plugins-tests.log`。
- Root 精选 Plugin control/live wiring 与 compact execution/supervision 20 tests / 29.247s OK，`extension-integration-tests.log`。实际覆盖启停、stage/apply、generation失效、风险/allowlist/命令刷新、生产 Application 五入口、有效连续写读与别名监督。
- 固定 probe 13 个规范操作、59 紧凑错误、33 底层错误及 Skill 两个边界；结果 `extension-probe.json`。
- 首次局部 unittest 缺 PYTHONPATH 导致导入失败，修正为显式 main root/src 后重跑通过；没有把失败计为产品 bug。测试使用现有隔离 fixture，不打开用户 DB、不调用付费模型。
- 本审计未作最终全量回归、真实 SDK 生命周期验收、真实模型参数 A/B，也未批准 S13 放行；这些由实现方与独立监督完成。
