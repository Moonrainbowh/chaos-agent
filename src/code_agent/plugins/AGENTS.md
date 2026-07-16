# Declarative Plugins
通过可审计、可撤销的声明式扩展注册事件、工具、命令、模式和自定义 Agent，同时保持 Host 的策略与完成门不可绕过。

## 边界
- 负责：发现并验证版本化插件 manifest、来源、namespace、digest、兼容范围、信任状态和显式激活状态。
- 负责：以不可变贡献快照提供事件订阅、工具描述、命令描述、Agent 模式和自定义 Agent 描述；只在空闲或下一任务边界原子替换。
- 负责：隔离重复 ID、冲突 namespace、超时、递归事件和插件失败；内建贡献不得被覆盖。
- 负责：事件订阅只接收脱敏、不可变的事件投影，并只能返回有界提案，不能修改、抑制或伪造 Core 事件。
- 负责：插件 UI 只能声明由 Host 统一渲染的 `notify`、`confirm`、`input`、`select` 交互；不得直接写 ANSI、接管终端输入或注入任意 Web DOM。
- 负责：插件工具只能映射到 Host typed action 或已启用、已批准的 MCP 工具；首版不接受 shell、Python callback、任意 URL 或进程内动态代码。
- 负责：插件命令只委托已注册控制器；插件模式和 Agent 描述必须通过 Orchestration 校验，并且只能继承或收紧能力。
- 负责：记录激活、停用、digest 变化、贡献来源和提案结果；撤销后立即阻止新的调用。
- 不负责：安装或更新插件、启动未批准 MCP server、读取 provider 密钥、直接执行网络、文件或进程动作。
- 不负责：改变 `ActionPolicy`、替用户批准动作、直接写入 sessions、evidence ledger 或任务终态。
- 不负责：伪造用户交互结果；Host 必须把用户响应建模为 typed、可取消且可审计的结果。
- 插件声明的风险只能提高或补充 Host 风险分类，不能降低风险；未知工具和未知映射保持拒绝。
- 插件引用 Skill 时仍由 `SkillActivation` 决定激活；Skill 不因插件引用而变成可执行代码。
- 插件产生的外部效果必须转换为 typed `ActionRequest` 并经过中央 `ActionPolicy`。

## Units
- `load_manifest(path, trust_store, ...): PluginManifest`：读取有界 JSON manifest，校验 schema、兼容版本、digest、信任和激活状态 | 文件读取 | 拒绝符号链接、未知动态代码字段和 digest 漂移。
- `manifest_digest(raw): str`：对去除自声明 digest 的规范 JSON 计算 SHA-256 | 无副作用 | 用于信任绑定和变更审计。
- `PluginRegistryBuilder.build(manifests): ContributionSnapshot`：校验并隔离工具、命令、模式、自定义 Agent 与事件贡献 | 无副作用 | 内建 namespace/ID 不可覆盖，风险、工具和推理强度只能保持或收紧。
- `PluginHost.stage(snapshot)`、`PluginHost.apply(task_active)`：在任务边界原子替换不可变贡献快照 | 进程内状态 | 活动任务期间不热替换。
- `PluginHost.revoke(plugin_id): bool`：立即阻止已撤销插件产生新的贡献调用 | 进程内状态 | 不等待下一任务边界。
- `EventProjection`：保存脱敏、不可变的 Core 事件投影 | 无副作用 | 禁止密钥、原始 reasoning 和原始工具输出字段。
- `DeclarativeEventRouter.route(event, ...): tuple[PluginProposal, ...]`：把事件订阅映射为有界 Host UI 或 typed Action 提案 | 无副作用 | 限制递归深度和提案数量，不执行提案或伪造事件。
- `UiRequest`：声明 Host 渲染的 `notify`、`confirm`、`input`、`select` 交互 | 无副作用 | 插件不能写 ANSI、接管输入或提供用户答案。
