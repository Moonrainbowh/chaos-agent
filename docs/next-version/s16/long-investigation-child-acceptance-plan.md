# S16 剩余真实调查与只读子任务：只读方案核验

状态：**PREPARED / OFFLINE_PREFLIGHT_ONLY / REAL_NOT_EXECUTED**。已创建独有 fixture、独有预检会话状态及声明式信任记录；没有 Provider 请求、真实子任务或真实换窗，没有修改产品与个人配置。候选绑定 `463dc98099e3275a70daf896507b02d4ae387ae9`；执行门会核验 HEAD 未变化。

更新：首个owned `b5c33bd81b35`的实际启动因TaskContract objective超过1,024字符被拒，HTTP0且失败记录保留。随后更完整离线入口预检发现Workflow title还要求≤512，`d5eb1e1852f7`仅离线失败、HTTP0。新prompt压至≤512，完整原流程冻结在owned AGENTS.md和docs/acceptance-procedure.md，当前用户目标明确授权按此范围执行，该来源不额外授予权限。最终新owned `f24c076ce446`显式关联旧失败，公开tasks.start真正冻结契约成功、任务CREATED、未启动events；HTTP0/exit0/stderr0/hash不变，未复用旧执行marker。该预检证明创建入口，仍不证明真实模型执行。

结论：本次经明确授权选择**独立 owned persistent 手动换窗样例**：多步来源分析→保存虚拟 notes 与真实 history item_id→显式 new_context 一次→下一窗读 notes 与历史原文→恰好一个只读 child。原个人 semantic profile 与原 semantic 实测不改、不替代。总 prompt 300,000、工具预算20,000、原模型/medium保留；work_tokens为原profile窗口1,000,000，没有降窗口制造容量耗尽。

## 契约与当前入口限制

- S16 原任务卡要求真实模型覆盖长调查、多窗、子任务；没有对应真实证据就不得宣布通过。来源：`C:/Users/Windows11/Downloads/chaos-agent-next-version-plan-20261004.md:711-742`，尤其724、732；当前授权边界见 `real-acceptance-plan.md`。
- 当前 `run_real_cli.py` 只接受 analysis/modify/noop/unverified/denied，使用原 semantic profile，未选择 team。原始 `python -m chaos_agent.cli --profile ... --mode medium run --json ...` 不提供 topology 参数；不能仅在 prompt 中要求委派便声称进入了 team。
- 公共 Host 控制链足够：`create_application` → `app.runtime_selection.use(topology='team', profile='glm-5-3-flash', reasoning_effort='medium', idle=True)` → `app.tui.task_modes.use('code', idle=True)` → `app.tasks.start(prompt)` → `app.tasks.events(task.id)` → `app.tasks.result(task.id)`，最后 `app.aclose()`。真正 Provider 保持原生产 client，不能替成 ScriptModel/FakeModel。
- 若沿用 CLI JSON 命令执行器，可由**外部验收 wrapper**捕获创建出的 application，并在生产 `execute_command` 前只调用上述公共 idle 控制；须将入口标记为“production CLI command executor with public Host team/code setup”，不是未经 wrapper 的原 CLI。不要修改 engine、dispatcher、Context 或 Provider 来强迫结果。更简单的公共 TaskService harness 则明确记录“actual Host TaskService”，不冒充 CLI。

## 同模型、同 effort 的真正只读子任务

内置 `search/librarian` 路由 low，`review/oracle` 路由 high；实际 child client 使用子 mode 的 effort，不继承 parent medium。`role='subagent'` 路由 medium，但其默认能力可写。不能把这些差异隐藏在结果中。

最小满足方式是现有声明式 AgentContribution：在**本次独有状态目录**注册一个 `s16accept.sourceaudit`，`base_mode='medium'`、`may_write=false`，`tool_names=['read_file','read_code_slices','list_files','search_text']`。该贡献没有动态代码、shell、网络、其他 thread、peer 或递归委派工具；所有 mode profile binding 都显式指向本次 glm-5-3-flash。medium base 给子 client 同一模型与 medium effort，`may_write=false` 又在生产 ChildRunner 剥离 write/execute 授权。

使用标准 JSON manifest 的必要字段 `id/namespace/version/host_api/digest/enabled/contributions`，Agent 项必要字段 `id/base_mode/instructions/tool_names/may_write`。计算真实 `manifest_digest(raw)`；只在本次 `LOCALAPPDATA/chaos-agent/plugin-trust.json` 保存本次 manifest 的 id→digest，不碰用户真实信任库。manifest 可放本次 `LOCALAPPDATA/chaos-agent/plugins/s16-acceptance/plugin.json`，无需改工作区内容或产品。生产加载器验证 schema/digest/信任后发布贡献。

父任务必须选择 interaction `code`：Host 将 `delegate_agent` 分类为 write，ask/plan 的冻结授权会拒绝委派，即使子目标只读。这个 code 选择只用于自有隔离项目内允许实际委派；父 prompt 严禁修改/执行，所有源文件前后 hash 仍必须相同。子任务的能力只读由上述 Agent 声明和继承授权实际保证，不能仅靠 prompt。

生产路径证据：

|事实|源码入口|
|---|---|
|builtin 角色固定 low/medium/high 路由|`chaos_agent/agent_modes.py:92`|
|child 实际 client 的 profile 与 effort|`chaos_agent/runtime_dispatcher_factory.py:49`|
|声明式 Agent schema 与 medium base|`src/code_agent/plugins/manifest.py:149`，`src/code_agent/orchestration/plugin_extensions.py:87`|
|owned manifest 与 trust 发现路径|`chaos_agent/plugin_runtime.py:242`、`:273`、`:288`|
|delegate 只在 team 暴露|`chaos_agent/runtime_dispatcher_factory.py:106`|
|delegate 中央风险映射 write|`chaos_agent/host_composition.py:100`|
|ask/plan 剥离父写与执行授权|`src/code_agent/interfaces/task_controller.py:532`|
|typed 父归属、同根、只读 child 授权|`chaos_agent/child_runner.py:53-115`|
|返回 advisory/result/usage/references|`chaos_agent/subagents.py:145`|

## 固定的最小隔离项目

复用已接受的小型 names fixture，所有文件在执行前冻结内容与 hash；仅添加两份必要来源文件，避免靠无意义大文本耗尽窗口。

```text
owned/workspace/
  AGENTS.md                  本次范围，只准读；明确允许一次 s16accept.sourceaudit
  names.py                   已知仍为 return list(values) 的旧实现
  test_names.py              不变的5项标准库测试源码
  pyproject.toml             原fixture的项目元数据
  docs/current-contract.md   trim/omit blank/order/duplicates/no mutation 5条当前约定
  docs/legacy-notes.md       清楚标注“历史建议，已废弃”的sort/dedupe提案
```

任务只读，不运行测试；测试源码不是通过证据。当前约定来自 AGENTS/current-contract，legacy 是待比较的历史资料，不升级为规则。预先冻结参考答案：旧函数只复制 iterable、不 trim/omit blank；重复与顺序会保留、不改原输入。最终回答必须给出五条约束的来源 `path:line`、代码路径和测试目标映射，明确“未执行测试”，不能声称 verified。

固定 prompt 完整保存在脚本与 fixture.json。父先成组加载6个契约（notes_write/read、history_list/read、new_context、delegate）；读取三个来源并取得真实历史ID；将约束、阶段结论、ID写入虚拟 investigation.md，随后显式 new_context 一次。换窗后读取该note与旧历史原文，再调用 `delegate_agent(agent_id='s16accept.sourceaudit', objective='Read docs/current-contract.md and docs/legacy-notes.md. Compare current versus superseded constraints with path:line references. No edits, commands, external sources, or delegation.', token_budget=30000, tool_budget=4, active_seconds=90)`；不要同时传 role。父检查子建议并完成五约束映射。

“必须调用”仍是模型行为请求，不是已执行事实。若模型没有真实调用、调用两次、改用 builtin 角色、传错预算或 child 没有终态，记录本次失败，不人工注入一次成功工具结果。

## 一次有界尝试与收集物证

保持原模型 `glm-5.3-flash`/medium、总 prompt **300,000**、工具预算20,000、API context_window=1,000,000。独有配置策略persistent，work_tokens=1,000,000，其余WindowPolicy生产默认：safety16,000/task5,000,000。Host有效单请求上限仍为300k减安全余量，不扩充API。每个client输出上限4,096。父子共享硬上限8模型回合、24工具调用；子局部30,000 token/4工具/90活动秒。整个attempt300秒、只启动一次，不人工重跑；280秒开始公开CancellationToken与TaskService.interrupt清理，外层Windows Job先assign后stdin释放worker，300秒硬截止杀掉全部Job进程并确认清理。

**配置纠正**：ProviderConfig生产默认timeout_s=60、max_retries=2。Config契约没有承诺timeout/retries TOML字段；provider_settings.py仅传provider_auth_options，后者只解析认证和协议路径，因此先前验收wrapper写入timeout_seconds=90/max_retries=0没有生效。新脚本移除无效字段并预检实际60/2，不私有patch、不扩产品配置；本次明确采用原生产默认重试策略。旧样例具体是否发生重试必须核对其实际request物证，不能从配置缺陷倒推实际次数。

预计父5回合：加载；读来源+历史索引；写note+new_context；换窗读note/原文+委派；最后回答。child读取两来源+回答2回合，共享约7回合/17工具。不同模型行为可能耗尽硬限，不能强迫或注入成功结果。HTTP审计记录每个实际请求model/effort/output，生产usage记录负责结算；不把model回合当所有HTTP尝试数。

无Provider预检已核候选HEAD、manifest digest、文件hash、实际team/medium/code配置、生产tool_catalog中六个必要工具、custom child同模型medium且may_write=false、8/24/4096/300秒、真实重试2/timeout60、总300k/工具20k。HTTP send被禁止，实际HTTP0。真实入口是公共Host TaskService，不冒充未经wrapper的CLI标准验收。所有产物仅在docs/next-version/s16/owned-cases的owned目录，凭据不落盘、不进候选Git。

结束后只读**本次自有会话库**，通过公开 Sessions 接口收集：

1. `app.subagents.subscribe` 的 queued/running/稳定终态快照；子返回的run_id和thread references；`app.subagents.child_thread(run_id)`；`sessions.load_thread_relation(child_thread)` 的 parent_thread_id。
2. `sessions.load_task(parent_task)`、`load_task_for_thread(child_thread)`：同一workspace_root，child冻结allow_workspace_write=false、allow_local_execute=false、allow_network=false、allow_outside_workspace=false；实际client请求的model/effort/output安全元数据也须一致。
3. child `context:child_budget` 持久绑定：owner_thread_id、parent_task_id、delegate_request_id、owner_instance_id、局部限额。可用 `sessions.list_checkpoints(child_thread)` 提取该本次checkpoint，不能从模型摘要猜父归属。
4. `sessions.load_task_budget(parent_task)` 和child task预算：根owner累计model_turns≤8、tool_calls≤24；`sessions.context_records(owner_thread,'usage')` 以唯一request id计算主/子/辅助实际input/output，按origin_thread_id分组。child usage是owner记录的子集，禁止相加重复计费。所有status必须settled才能宣称usage完整；pending/partial保unknown和预留负债。
5. 父/子 result真实终态、子advisory=true、usage.complete=true、来源引用能定位到冻结文件；父source逐条映射正确。整个源目录前后递归清单与hash相同；会话/usage/window/虚拟notes写入属于独有状态，不是源文件修改。

预算事务与结算证据：`src/code_agent/sessions/_task_budget.py:95-115`、`_shared_budget.py:54`、`_context_journal.py:30/64/95`。父关闭前取消并等待子清理由 `chaos_agent/subagents.py:266` 保证，但成功样例**不证明实际取消场景**；若本次未发生取消，应保留真实取消验收未完成，不从离线测试外推。

## 跨窗与“长调查”的诚实边界

当前semantic默认仍保留 `max_message_tokens=12,000`（`context/budget.py:67`）；Host `_semantic_limits` 以此构造历史压缩阈值（`application_context.py:235`）。总prompt300k不等于历史原文一直保300k。自然发生且有真实Provider记录的semantic checkpoint只能证明摘要与来源索引，不能变成managed WindowJournal换窗证据。

本次独立persistent样例使用现有公开 `new_context(reason=...)` 主动请求换窗。该工具持久request checkpoint，下一完整工具组关闭后的构建触发persistent重置；window checkpoint带source_start/end/digest、start_sequence、number、request_id与reason=requested。初始虚拟window0，加一次真实window记录number1代表两个窗口，不能要求伪造两条window记录。须由真实parent事件证实new_context一次、下一实际Provider请求使用新窗、notes和旧history读取成功；同parent task/thread/owner绑定与context-budget-observations证明累计预算不重置。请求存在但未发生下一个真实构建/Provider调用，不能通过。

此例不填充大文本、不降work预算，证明手动持久窗切换与来源恢复，**不证明自然容量触发/300k长文本性能**；后者仍未完成。既有semantic摘要亦不能代替persistent证据。

这个任务验证的是一次有界来源调查、约束区分和真实只读child，不代表持续数小时调查、一般长任务成功率或性能提升。没有相同参数旧版本实测对照，仍不作总体改善结论。

## 停止条件与本轮交付

本轮交付报告、run_real_investigation.py、owned准备与HTTP0预检。没有真实child/window/PASS；收费执行由Root核验后单次启动。遇usage未完整、超时、硬预算耗尽、作用域/修改、model/effort偏差、未决动作或失败，原样保留并停止，不放宽阈值或重新启动补PASS。
