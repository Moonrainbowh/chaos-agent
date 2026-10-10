# S16 Context 无 Provider 独立诊断

结论：当前默认 2,000 工具额度不能覆盖正常 Host 内置工具。最初非Git、semantic、single 的 1,977 首轮可以构建；Git 项目默认首轮 2,025 即失败。fixture 原先显式 persistent 与用户真实 profile `context_policy=None` 不一致，必须恢复该策略以验证真实用户路径，同时产品额度缺口也确实存在。

候选 HEAD：`90e4879a082a13ed3413a5a4337cb5ddace48250`。本诊断未修改产品、真实数据库或私人配置。临时状态和 synthetic workspace 均为本次独有 Temp 目录；Provider 使用 dummy key，HTTP send 与外部 socket.connect 在诊断脚本中被阻断。16 个生产 CLI 组合的 `provider_calls=0`、`network_attempts=0`。实际 Context 构建成功时也强制 `DIAGNOSTIC_STOP_BEFORE_PROVIDER`，因此最终 CLI 的 failed 不是构建失败判断依据，JSON `context_success` 与完整 cause chain 才是依据。team 通过生产 `runtime_selection.use(topology='team', idle=True)` 切换，Git 项目在独有Temp使用 `git init`。

## 真实目录结果

以下是 WorkspaceContextBuilder 的现有 `_render_tools` + `estimate_tokens` 口径，完整披露使用真实 Host dispatcher.tools 和 Core `progressive_tools` 当前 digest；不是 API usage。

|策略|非Git single 首轮/全披露|非Git team 首轮/全披露|Git single 首轮/全披露|Git team 首轮/全披露|
|---|---:|---:|---:|---:|
|semantic|1977/2647|2014/2833|2025/2695|2062/2881|
|summary|2070/3001|2106/3188|2118/3050|2155/3236|
|boundary|2070/3001|2106/3188|2118/3050|2155/3236|
|persistent|2327/3882|2364/4068|2375/3930|2412/4117|

当前用户原 profile 只读取并记录非敏感属性：model `glm-5.3-flash`、context_policy `None`、context_window `1,000,000`、max_output_tokens `131,000`。诊断保留 S16 fixture 的 output 4,096，不调用 resolve_api_key、不输出 key_status。

## 最小反例与因果

1. 独有Git项目 + 用户原semantic策略 + single + medium + 标准内置工具：初始目录 2,025 > 2,000，在模型前失败。相同非Git项目 1,977 成功。Git status/diff 加入 execute 的完整 schema/description 后使目录净增约48。
2. 非Git、semantic、single 的真实首次目录 1,977；合法加载一个 `list_agents` 契约之后，新回合 Core 当前 digest 投影 2,025 > 2,000。其他合法单项披露 `read_thread=2,102`、`search_threads=2,067`、`web=2,281` 也失败。
3. `load_tool_contract('read_file')` 在 RestrictedDispatcher 中别名化到 `read`，后者已经 eager；此例仍1,977，不是失败反例。
4. persistent 首轮增加的350全部来自 load_tool_contract 目录（431→781 tokens）；五个主工具的 schema/description 完全相同。11项 History/Notes/new_context 还未被完整披露，但它们的名称枚举及摘要已进入目录。

## 最小正确修复建议

将 `PromptBudget.max_tool_tokens` 有界默认值改为 4,500，覆盖实测内置最高4,117并留383余量；同时更新 Context 契约的旧2,000文字。保留显式配置2,000与所有失败逻辑。总prompt仍20,000、系统/规则上限、Host安全余量1,664、最小message2,000、API/window/task限制、完整工具schema与token估计全部不改。可选Repo Map按现有allocator规则先收缩；最大4117 + system/rules约1217 + task_state31 + safety1664时messages仍可保12,000，Repo Map约971，总不超过20,000。

压缩冗余目录摘要可以优化，但不能单独解决产品契约：即使删除所有 loader 文本，真实完整内置schema/description的raw catalogue已超过2,000（semantic单人2,282、persistent单人3,168；Git/team更多）。仅升到2,327/2,500会在后续合法披露失败，4,000仍不能覆盖team+persistent+Git的4,117。不要删schema、删required/safety文义、调整估计器或修改测试阈值伪造通过。

## 回归建议

根集成测试在隔离配置/Temp Workspace创建真实 create_application，client_factory使用禁止stream的Fake Provider。用实际engine/actions.tools()经Core advertised_tools/progressive_tools投影，覆盖四策略 × single/team × Git/nonGit的初始及全披露；默认值应允许构建，显式2,000应保留上述可验证反例。保留真实工具digest、schema、mode/permission路径，断言无Provider调用。选择Git首轮反例及非Git list_agents单项披露反例作为最小生产回归。另断言降低total使固定前缀无法保留min_message时仍失败、超tool显式额度仍失败。

可重复脚本：`context_diagnosis_probe.py semantic single`；追加第三参数 `git` 创建独有Git项目，例如 `context_diagnosis_probe.py persistent team git`。脚本是诊断工具，不是生产测试已通过的声明。16格原始JSON及聚合 `context-diagnosis.json` 保留修复前数据；产品修复后应重新生成另名证据，不能覆盖本反例物证。
