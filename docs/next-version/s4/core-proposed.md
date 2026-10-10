# Agent Core
有界可取消事件循环，仅通过抽象 model/context/dispatcher/sessions/verification 协议协调；不直接访问API、文件、进程、数据库、终端，不实现快照/journal/coverage/rewind UI，不导入具体verification/projects adapter，不绕过权限。

## 边界与必需约束
- 原调用名/ID用于真实执行、日志和消息配对；底层身份用于监督/TaskState/验证。仅Host确定性resolve_supervision_action（兼容resolve_action）解析，模型元数据不可信；旧名称限已披露操作，未知/解析失败不算进展，解析不能代替权限。
- 模型/动作前读取持久预算与控制；耗尽稳定paused且零provider/工具。任务回合/tool/token/stall跨恢复累计，usage单调、软租约≤硬上限；active-time遥测累计，每次run独立时间段；不由context_window+output推导总额度。语义构建消耗同一冻结model/profile/token/time/cancel预算。预算不足TaskRecord持久paused，无TaskRecord明确错误。
- ask/plan关闭workspace write与local execute，旧缺字段兼容code。普通问候、中英只读问答、否定修改（不要修改/无需修改/只解释/dont edit等）判analysis，不因写词误判；明确肯定写要求保留修改门，不改持久意图。无实际改动不得跑无关测试/伪完成，须继续实现或说明无需修改；模型和验证均停止而改动缺证据，进入有原因等待决定，不无限VERIFYING。
- structured验证开启时COMPLETED仅由最新contract/subject generation/required evidence系统评估；模型文字不产生证据，验收不可降级。关闭时实际改动可完成但stop reason标未结构化验证；无改动明确未实现，不把未跑测试标通过。ACCEPTED_PARTIAL只由明确用户决定；SUPERSEDED不可恢复执行。状态先持久再发布。
- Context只接不可变ContextRequest，同revision最多调用一次；legacy兼容调用前判断，不因内部TypeError重试。透传真实thread/revision/cancellation，不从正文猜身份；task_facts仅Host核对的冻结标量JSON（mode/permission等），不从历史推断。上下文余量/预算/Repo估计是本地值，不是Provider Usage。
- 首回合/恢复/steering持久完整user输入与冻结附件引用；附件仅user，空正文有附件有效，两者皆空fail-closed。引用仅摘要/MIME/大小/显示名/可选尺寸且构造校验，不读blob/解析图/provider schema，不泄漏原路径/base64到事件/消息。
- ActionRequest/Result/ToolDefinition只JSON；Message/ToolCall构造校验冻结；owner/origin/task/request/parent request执行context显式不可变、有界非空ID；child继承parent lineage，不把自身origin/request放入父lineage。ad-hoc root owner/origin=active thread；不可用/dispatch前暂停无execution context。
- 工具失败/验证拒绝后下回合追加有界根因/假设/替代策略反思；同态上一失败调用直接阻断，优先Host失败摘要不泄漏长输出。精确只读重复与tool-only分别观察；参数验证失败不耗exploration_count，连续3次失败防循环。5回合无正文/写/verify/换窗进展，无可见回答则请求无工具总结，否则停；换窗重置，不能把多步只读调查判停滞。总结误请求工具仅重试一次，仍无正文发布可见错误后按证据进完成门。
- runtime developer notice仅配对闭合后持久、下一payload一次；已有非空无工具回答不重复总结。分析最终预算回合不dispatch，修改保留一次工具机会后进门；执行结果和tool消息落地后才响应取消，已开始写无论取消先可恢复记录，tool预算/验证顺序不改。
- steering下模型轮前消费；无工具轮、完成/验证门前原子FIFO提升follow-up，发布TASK_FOLLOWUPS_PROMOTED后续同任务，无虚假完成。
- 能力策略legacy/hybrid/progressive构造冻结；hybrid预热高频read，长尾契约成功读取后下一轮附schema；无loader保持全量。name+schema digest校验披露，MCP/plugin变化使旧披露失效；读契约不执行/扩权。未声明工具/重复ID/流无完成/预算越界fail-closed。
- peer run不伪造user消息，首合法模型事件落地后确认context，冻结peer allowlist默认空，无TaskAuthorization；peer正文不可信。SessionJournal拒绝不可信历史入context；对外错误不含上游文本，model stream cause供安全状态提取，不直接print traceback。
- Verification仅抽象协议：Host trusted typed verifier不含shell/argv/install参数，仍记成对assistant/tool并耗tool额度；logical change一次generation，L0失败携带已写后的TaskState、阻止同批剩余；关键风险final gate tests→build。原子assessment/outcome句柄不得伪造evidence；TaskIntent/criterion/revision不写旧core/models.py。
- 仅真实尝试失败的shell/process记失败事实，policy/preflight拒绝不伪装执行失败；可信进展来自Host generation/subject/evidence/action/control，模型自述不可信，嵌套冻结JSON先plain再指纹。
- MODEL_STARTED记真实模型，保存最近披露名供Host；Usage输入含缓存、cache读写/可用性兼容旧数据。AgentEvent生成UTC；CONTEXT_BUILT只白名单非负整数本地计数，PHASE_COMPLETED仅有界phase/duration/action，无prompt/output；phase计时单调非负有界不计active-time。trace默认Host启、CHAOS_DEBUG_TRACE=0关，只日志有界计数/短digest，不含prompt/源码/凭据/完整路径。

## Units
- AgentEngine及Dispatch/Convergence/Completion mixin：抽象模型回合、配对结果、持久状态/验证门；外部副作用由注入协议负责。
- resolve_supervision_call/operation_kind、TaskSupervisor、ExplorationRepeatObserver/ToolOnlyConvergenceGuard：纯身份解析、预算/失败/重复监督。
- TaskContract/TaskRecord/TaskState/TaskBudget、ContextRequest、ActionExecutionContext/Lineage：冻结可序列化任务事实；reduce_task_state与completion assessment为纯函数。
- CancellationToken传播首次原因并唤醒；SessionJournal组合会话错误；EngineLimits与BudgetLease选择有界额度，不扩授权/沙箱/网络/Provider能力。

本地数值白名单保留prompt_budget_tokens/prompt_safety_tokens/prompt_estimated_tokens/repo_context_budget_tokens/repo_context_estimated_tokens。历史实验见docs/context-boundary-experiment.md与results.md，候选实验不自动推广默认。
