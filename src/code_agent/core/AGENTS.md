# Agent Core
有界可取消事件循环，仅通过抽象 model/context/dispatcher/sessions/verification 协议协调；不直接访问API、文件、进程、数据库、终端，不实现快照/journal/coverage/rewind UI，不导入具体verification/projects adapter，不绕过权限。

## 边界与必需约束
- S11：ContextRequest.project_memory 为≤16KiB不可信引用文本，独立于trusted task_facts，不增权限/验证事实/任务状态；replace保引用，缺省空兼容。
- S10：新 Task 保存非敏感不可变上下文选择快照，恢复核对漂移；旧契约缺字段保持兼容，不补造历史策略。
- S10：默认历史依赖有界页和持久增量游标，旧库首次分页重放；热路径只处理新消息。恢复配对独立于上下文tail，必要完整工具组过大请求前失败。
- Host progress 缓存绑定 objective/candidate_limit/hard_tool_limit；租约候选上限变化或目标变化一次有界重放，替换CAS使用原持久cursor，后续同参数继续增量，不继承旧配额下截断或错误作用域的事实。
- S9：收敛仅采信成功动作的新信息、内容代际、验证结果或失败解决等Host事实；正文/换说法/工具名/空换窗不重置停滞或续预算。有效长调查/多文件修改可继续，重复失败先具体反馈再有界暂停；硬预算、持久恢复与S6结果契约不变，不建第二监督器。
- 有界候选包含已核对的新schema披露及真实本地history/notes读取，只缓解停滞、不续租/证明完成；正文写入notes不生成proof。路径/全仓/deep只取明确正向子句，读禁路径排除优先。验证进展是已闭合历史成功摘要，当前proof仍按最新FAIL/UNAVAILABLE撤销旧PASS。
- 恢复前检查完整持久消息中的未配对动作，不因WAITING_DECISION、重复续接、普通thread入口或重启绕过；未知副作用先等待。解决决定必须绑定task/thread/call和原消息版本，理由/证据持久化；不重放已知动作，不把人工说明当验证证据。
- 原调用名/ID用于真实执行、日志和消息配对；底层身份用于监督/TaskState/验证。仅Host确定性resolve_supervision_action（兼容resolve_action）解析，模型元数据不可信；旧名称限已披露操作，未知/解析失败不算进展，解析不能代替权限。
- 模型/动作前读取持久预算与控制；耗尽稳定paused且零provider/工具。任务回合/tool/token/stall跨恢复累计，usage单调、软租约≤硬上限；active-time遥测累计，每次run独立时间段；不由context_window+output推导总额度。语义构建消耗同一冻结model/profile/token/time/cancel预算。预算不足TaskRecord持久paused，无TaskRecord明确错误。
- ask/plan关闭workspace write与local execute，旧缺字段兼容code。普通问候、中英只读问答、否定修改（不要修改/无需修改/只解释/dont edit等）判analysis，不因写词误判；明确肯定写要求保留修改门，不改持久意图。无实际改动不得跑无关测试/伪完成，须继续实现或说明无需修改；模型和验证均停止而改动缺证据，进入有原因等待决定，不无限VERIFYING。
- structured验证开启时COMPLETED仅由最新contract/subject generation/required evidence系统评估；模型文字不产生证据，验收不可降级。关闭时实际改动可完成但stop reason标未结构化验证；无改动明确未实现，不把未跑测试标通过。ACCEPTED_PARTIAL只由明确用户决定；SUPERSEDED不可恢复执行。状态先持久再发布。
- Context只接不可变ContextRequest，同revision最多调用一次；legacy兼容调用前判断，不因内部TypeError重试。透传真实thread/revision/cancellation，不从正文猜身份；task_facts仅Host核对的冻结标量JSON（mode/permission等），不从历史推断。上下文余量/预算/Repo估计是本地值，不是Provider Usage。
- 首回合/恢复/steering持久完整user输入与冻结附件引用；附件仅user，空正文有附件有效，两者皆空fail-closed。引用仅摘要/MIME/大小/显示名/可选尺寸且构造校验，不读blob/解析图/provider schema，不泄漏原路径/base64到事件/消息。
- ActionRequest/Result/ToolDefinition只JSON；Message/ToolCall构造校验冻结；owner/origin/task/request/parent request执行context显式不可变、有界非空ID；child继承parent lineage，不把自身origin/request放入父lineage。ad-hoc root owner/origin=active thread；不可用/dispatch前暂停无execution context。
- 工具失败/验证拒绝后下回合追加有界根因/假设/替代策略反思；同态上一失败调用直接阻断，优先Host失败摘要不泄漏长输出。参数验证失败不耗exploration_count，连续3次失败防循环。5回合无新Host事实，无可见回答则请求无工具总结，否则按证据停止；正文/换窗/工具名不清零。成功读取候选最多初始软工具额度，只有冻结明确路径/全仓范围或明确symbol搜索的实际路径链构成相关读；累计去重不淘汰重收。逻辑generation/当前subject的可信新PASS/同签名真实失败解决可续租，新失败/普通exit0不可；硬预算及三次续租上限不变。总结误请求工具仅重试一次，仍无正文发布可见错误后按证据进完成门。
- runtime developer notice仅配对闭合后持久、下一payload一次；已有非空无工具回答不重复总结。分析最终预算回合不dispatch，修改保留一次工具机会后进门；执行结果和tool消息落地后才响应取消，已开始写无论取消先可恢复记录，tool预算/验证顺序不改。
- steering下模型轮前消费；无工具轮、完成/验证门前原子FIFO提升follow-up，发布TASK_FOLLOWUPS_PROMOTED后续同任务，无虚假完成。
- 能力策略legacy/hybrid/progressive构造冻结；hybrid预热高频read，长尾契约成功读取后下一轮附schema；无loader保持全量。name+schema digest校验披露，MCP/plugin变化使旧披露失效；读契约不执行/扩权。未声明工具/重复ID/流无完成/预算越界fail-closed。
- peer run不伪造user消息，首合法模型事件落地后确认context，冻结peer allowlist默认空，无TaskAuthorization；peer正文不可信。SessionJournal拒绝不可信历史入context；对外错误不含上游文本，model stream cause供安全状态提取，不直接print traceback。
- Verification仅抽象协议：Host trusted typed verifier不含shell/argv/install参数，仍记成对assistant/tool并耗tool额度；logical change一次generation，L0失败携带已写后的TaskState、阻止同批剩余；关键风险final gate tests→build。原子assessment/outcome句柄不得伪造evidence；TaskIntent/criterion/revision不写旧core/models.py。
- 仅真实尝试失败的shell/process记失败事实，policy/preflight拒绝不伪装执行失败；可信进展来自Host generation/subject/evidence/action/control，模型自述不可信，嵌套冻结JSON先plain再指纹。
- MODEL_STARTED记真实模型，保存最近披露名供Host；Usage输入含缓存、cache读写/可用性兼容旧数据。AgentEvent生成UTC；CONTEXT_BUILT只白名单非负整数本地计数，PHASE_COMPLETED仅有界phase/duration/action，无prompt/output；phase计时单调非负有界不计active-time。trace默认Host启、CHAOS_DEBUG_TRACE=0关，只日志有界计数/短digest，不含prompt/源码/凭据/完整路径。

## Units
- `ParentReviewModel`：Host 注入独立 client/model_name/深冻结非敏感 identity；仅 active independent/comparison（含原阶段补全）且快照 `review_model` 严格匹配时切换，每个请求的 stream、MODEL_STARTED 和 usage 同用该选择。持久绑定缺配置或漂移在 MODEL_STARTED/provider 前以 ModelStreamError 闭合；旧快照无绑定与普通/child 保留原模型。沿用原预算，usage 依据实际 client 的 accounts_task_usage 防止双计，不改 engine 主模型或 context runtime selection。
- `ParentReviewHost`/`ParentReviewSnapshot`：仅 ANALYZE 的真实 required-source 委派启用父复核；Core 消费 Host 冻结全文、内容版本、物理行号与持久阶段。父同任务两个实际无工具请求，独立阶段绕过普通 context builder，隔离 advisory/历史/摘要/notes；后续比较同版本来源及初判。测试能力主张须在 judgment/rationale 内给出具体断言、静态结果和错误行为见证，限制结论范围；静态推演不冒充执行。v2模型仅选来源路径/物理行，Host绑定版本和提取原文；显式错误版本/引用仍拒绝，旧快照保留v1严格契约。三项源码维度及比较必须有来源，任务/子目标的流程事实可引用本阶段Host runtime证据；运行状态不证明语义。补全只带同阶段原稿和具体字段错误，Host仅允许修改重新核验出的错误JSON pointer，再完整校验；缺项全程最多一次原预算补全。无工具完整响应的每次结构校验结果（含末次拒绝）通过Host与状态原子保存独立审计，容量不足保留原文并阻断；不覆盖流中断或工具协议拒绝的其他路径。共享终结门覆盖软租约，硬预算/暂停保留 remaining，取消不扩额度。内部JSON不进入普通消息；最终正常正文与delivered原子持久化，结构/引用/覆盖门不证明语义正确或升级verification。
- S16 source completion：仅消费 Host typed snapshot；可选必要完整来源在子启动冻结，无工具结束缺项时原预算内持久反馈，有反馈而无新增必要读取则 failed/source_requirements_unmet/remaining。取消和硬预算优先；读齐还须当前非空最终答复，来源门不升级 verification、不判断正文语义；无要求保持原行为。
- 父复核v2把独立ATX标题全文并入相邻正文组，保留正文原ID并要求覆盖所有正文；旧标题ID可额外引用但不能替代正文，纯标题报告仍需覆盖，v1段落协议不变。每次请求只披露实际接受的一种输出格式：正常/不可解析原稿为完整报告，可解析待修稿为字段补丁；旧v2补全恢复后若原稿完整重验已无错，仅接受显式空补丁确认保留，不改原稿、不重置repair_used或增加额度。标题文字和代码块内容不会因分组消失。
- 父复核v3在原两阶段请求中用独立`assertion_checks`表达测试见证，替代v1/v2把见证全放入自由文本的约定。Host只比较普通JSON字面值，重算当前观测pass/fail，拒绝不失败的detected、不通过的missed及没有失败契约反例的missed；scope限定witness_only，未知观测/见证必须显式说明。比较不执行源码、不验证静态推演或自然语言语义，最终仍unverified；不得由一个见证推出全称覆盖结论。v3隔离请求显式保留简体中文交付，来源字面值、标识及子报告原话不翻译。只对新Host快照启用v3，旧v1/v2持久记录不迁移、不追加新补全额度。
- 父复核v4只收紧新快照的比较响应：每个初判finding/assertion必须恰好确认保留或完整更新，可追加本阶段要求和见证；unknowns与comparisons完整重写。初判全文、来源与子报告仍完整送入模型。Host合成完整报告后沿用全部引用、覆盖及见证校验，保留原始差量和effective完整稿；差量/完整稿的字段修复共用原有一次额度。确认ID只表示模型选择保留，不证明语义正确；旧v1-v3继续完整报告协议。child_objective本来只在比较阶段披露，新增该finding不属于初判漏项。
- 新任务初始软租约：明确 deep 优先，Host 冻结 topology=team 至少 STANDARD（也包括简单团队问答），single/legacy analyze 保持 QUICK。硬额度和 child 不可续父租约不变；已持久预算不迁移，不把该 floor 当复杂度判断。
- `SourceCompletionSnapshot`/`SourceCompletionHost`：有界要求、已读取集合与持久纠正基线 | 纯类型及注入协议 | Core 不读文件、解析工具结果或访问 SQLite。
- 新任务 intent：只读限定支持 read only / readonly / 常用连字符 read-only；文件名中的限定词与明确肯定修改命令不构成只读任务。仅新任务冻结时分类，不重算既有持久 TaskContract.intent，不改变 TaskAuthorization。
- `AgentEngine.inherited_authorization`：子执行继承冻结父任务授权并使用真实子 thread 作为动作 origin；须绑定具体父 task lineage，不向子 Core 注入父 TaskRecord，不使子结果完成或验证父任务。
- TaskResult/ResultCollector：有界交付投影，执行/变更/验证/剩余项分开；旧完成事件不证明验证，无终态流不冒报成功，不另存权威状态。
- pending_calls：按持久顺序与原ID/name配对未决动作；重复ID保守阻断，模型续接不可重用旧ID。
- AgentEngine及Dispatch/Convergence/Completion mixin：抽象模型回合、配对结果、持久状态/验证门；外部副作用由注入协议负责。
- resolve_supervision_call/operation_kind、TaskSupervisor、ExplorationRepeatObserver/ToolOnlyConvergenceGuard：纯身份解析、预算/失败/重复监督。
- TaskContract/TaskRecord/TaskState/TaskBudget、ContextRequest、ActionExecutionContext/Lineage：冻结可序列化任务事实；reduce_task_state与completion assessment为纯函数。
- CancellationToken传播首次原因并唤醒；SessionJournal组合会话错误；EngineLimits与BudgetLease选择有界额度，不扩授权/沙箱/网络/Provider能力。

本地数值白名单保留prompt_budget_tokens/prompt_safety_tokens/prompt_estimated_tokens/repo_context_budget_tokens/repo_context_estimated_tokens。历史实验见docs/context-boundary-experiment.md与results.md，候选实验不自动推广默认。


接口细节与历史说明（非默认规则）：`docs/next-version/s4/reference/core-before.md`。本文件已保留必要约束；参考快照不覆盖当前规则。
