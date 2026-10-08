# Application Integration
组合Feature为Windows/Linux/macOS共用TUI/CLI/JSON Host；发布`chaos-agent`并保旧`agent`与本地状态。只组合共享依赖与typed actions，不重写policy/workspace/runtime安全、Feature内部行为、另建Agent loop/索引/运行时；不自动翻译Shell、初始化Git或把失败报成功。

## 边界与强制约束
- S9：默认复用现有风险适配验证计划；轻量仅可信LOW/TRIVIAL证明，其余相关/全tests，关键tests+build。模型正文/普通exit0不充系统证据；显式兼容禁用只交付unverified，无需修改由操作者接受。
- TUI/CLI/ACP/手机共Foreground启停/恢复/决定/结果；Session持久消息树，Task冻结目标/授权/runtime，Attempt为其执行实例、不重建预算。终态对话继承消息建新Task，未终态走原恢复门；Workflow只投影事实、不定生命周期。
- Context显式交付builder/guarded client/actions/snapshot，不猜私有_inner。只读task list/result/recovery走仓储，不需Provider/Shell/plugins/UI；替代工厂先迁真实入口测试再核引用清旧，不删仍有安全职责实现。
- task recovery/resolve为操作者核对入口，非模型tool；本地写在mutation gate内核completed回执、task/call/owner、物理workspace fingerprint及当前文件hash；PREPARED/GAP/外部未知不猜成功，人工报告不充验证证据，不重放旧ID。

- 主模型默认read/search/write/edit/execute，Web按需web、load_tool_contract扩展发现；先过滤mode/role权限再compact，原名兼容限已披露operation。纯观察先compact再Host当前plugin target映射、保call ID，不建workspace服务；执行仍qualified插件名、原双层plugin/target风险策略；MCP/未知opaque且不认模型risk/progress。Root副作用前typed递归schema/preflight/中央policy/审批，再Workspace/Runtime执行，只有已准外部路径抵达Unit。WebAccessService source/task共享关闭HTTP，但启用不授网络权。
- single/team主可已注册list/send，delegate仅team；child禁delegate/peer协调，rename永不模型暴露；契约loader不扩权。child按已配置角色profile路由，实际预算/取消/单写者/冻结快照约束，结果advisory非验证evidence。plugins可信manifest/不可变贡献，plugin与target分别过policy。peer消息不授权，不泄正文；task-owned不后台恢复，taskless仅list/send；held/queued输入不可信，退避与runtime/peer共activity lock。
- 启动在Provider副作用前探测冻结PowerShell/POSIX方言，source/worktree/主子/切换共配置capability策略；prompt不泄exe全路径，本地status可核完整探测。方言不匹配/非零/Git故障结构化真实失败；PowerShell默认Stop、native原始字节，structured program+args无shell/env/stdin，拒shell launcher/.cmd/.bat/NUL/超限Windows command line。stdout/stderr分别严格解码/截断，不能解码完整Base64/codepage；legacy script仅旧Runtime兼容，file auto不猜legacy codepage。Windows长路径预算先于resolve/mkdir/进程，启动/status/prompt可见，不以missing掩策略。
- generation-bound slices每批1–16 targets，device64/file128位unsigned不截断；当前RepoIndex generation/signatures+Workspace读前后复核，任一stale仅stale_repo_context无部分源码；提示合并当前已知目标但新信息可再批。Git根list/search复用tracked+nonignored-untracked inventory后Workspace可见性过滤，守子gitignore；显式子目录/普通目录受保护递归。list默认25/max50、next_cursor；search root/include_globs/max_results限域，timeout保matches但complete=false/incomplete_reason=timeout，result_limit标截断，部分无命中不当完整。Git枚举算总deadline，失败不全盘fallback；预留修复验证预算查邻近行为。
- workspace默认auto/direct source，无隔离原因不探Git/dirty/复制worktree；direct恒source，managed独立Git worktree（非Git明确本地降级及原因），auto观察并发写者/background/explicit才managed，未知值拒启动。ContextVar仅声明本次隔离原因、边界reset不猜Git；无法满足required isolation先于thread/task拒、保already-active原因，Git创建失败不静默回source。同source仅一本地写者；isolated唯一seeding入口带dirty改动，本地不得走它；敏感路径独立opt-in所有source/task Guard共享。
- managed用LOCALAPPDATA/chaos-agent-workspaces短新根，与受保护API配置目录并列，新snapshot只写新根；旧chaos-agent/managed-workspaces/snapshots只构造时验证/missing-only只读fallback，不迁移/回填/GC/删除旧根/worktree。storage/repo中间目录不能workspace，worktrees/repo/lineage任务根可用。不建daemon/自动Git提交。准备/绑定失败补偿，已建task可恢复interrupted；local lineage source=worktree，不建分支目录/复制文件，最多4常数Git调用，缺事实/历史direct占用metadata-only不阻启动，其他SQLite故障报错。
- reclaim仅显式退役可证明无任何任务工作的注册worktree：live lineage/pending rewind/保护snapshot（rewindable=False）/缺失或未终态task均保留；branch必须task_branch_name、HEAD祖先source_head无自有提交、tip一致且干净；任校验失败停止保留，不把用户文件当垃圾。workspace marker只直属最多512条、不递归；Home/磁盘根即Git仍轻量。
- canonical root独立复用editor/snapshot/mutation gate/计划/capture/CoordinatedSession；source/task不共享gate/snapshot/plan，facade共唯一基础Sessions。Rewind SessionRouter按thread/owner root路由，未绑定child回owner再source；仅所选facade写checkpoint。首次写前PRE/POST durable journal，未知写者先gap，异常/取消owned-only恢复后才释gate，foreign conflict阻后写。启动batch恢复先于rewind，查询后加锁重读、不缓存/不跳conflicted，失败闭合、并发仅一次；task/thread目录已不存在可跳，source缺失仍构造校验不可吞。RewindRuntime双观测只读，无apply/restore/approval/provider/Git reset。
- edit计划Host不可变ID/digest、plan/apply分离、一次消费；apply重preflight，ignored/untracked既有文件显式风险，取消先于applying；回滚/预检冲突/拒绝不伪工作区变化，模型不能提交可信risk/Diff。verification source/task使用活动Context同代RepoIndex，不另索引、不信模型scope，root漂移闭合；只修改意图/实际变更刷新，问答无修改路径不跑无关验证。默认启用风险适配structured验证；CHAOS_STRUCTURED_VERIFICATION=0兼容禁用，隐藏/拒run_verification但保change tracking/普通命令，交付明确unverified；未执行不报通过。
- runtime topology/profile/effort三轴独立，四档mode绑定真实profile/统一工具集/Ultra硬预算/策略；profile不存在拒启动不任意fallback。空闲切换原子替换provider/runner/dispatcher/audit，构建/替换失败保旧；legacy/plugin mode只提示/投影不重置三轴，恢复model/protocol/host/digest漂移闭合。task digest冻结完整ModeSnapshot含实际tool set，plugin恢复不能回宽内置mode。清理任意BaseException的partial provider：主等待best-effort close、同步child保留调度async close，不盖原错误。这些runtime拆分Unit保持≤300行，TUI兼容profiles/list_profiles。Anthropic medium明确prompt-only、确认wire前传None，非默认effort及带非默认切入拒、不猜字段；max_output_tokens映max_tokens。
- 共享AttachmentStore/Ingestor与当前profile显式input modalities注入主/子/切换provider，memory动态profile可读取；自定义单参数model factory保兼容，Host不解析blob/猜能力。ApiModelControl显式Responses/Chat目录发现/缓存/确定性内存profile，protocol/auth/capacity/modality/budget继承配置；失败保旧，发现不切/不写默认，恢复由原配置重建。
- Authentication Controller隐藏key/授权码，按平台/auth slot影响检查；保存开始等原子结果，刷新失败不伪登录失败，WorkBuddy无离线种子可统一model显式加载，按auth缓存、重登清slot cache、失败保登录；Antigravity首层只本地目录二级、不联网。保存login profile由同名平台/auth槽及目录恢复task/上次选择，不写默认。auth CLI先于模型/workspace初始化，profile显式auth，配置追加前ProviderConfig验证，不替换旧profile/default、不因刷新推理。启动TUI保存模型按(name,model,protocol)验证，过期安全回配置默认，不把tuple当ModelProfile。
- 项目手机目录/历史恢复不依赖cd、不调模型；切项目关旧app以显式root重建，失败返列表、mobile显式compact。消息树分支核节点/冻结原runtime/消息前缀，不复制task状态/budget、不恢复文件；非终态原恢复，终态延续先恢复原模型、新任务继承消息不执行状态/预算、禁跨项目。用户!command有origin事件/不可信结果无模型，!!不入库，取消后持久真实结果。
- managed context按冻结profile组合summary/boundary/persistent及统一budget client；persistent无HandoffWriter。Project Memory由Host真实原根/lineage固定scope，显式用户CRUD/CAS、active适用查询与不可信预算前投影；外项目thread/ID拒绝，user关闭，不加权限/验证事实/第二库。模式只实际注册tools。共享RepoIndex后台预热不阻启动、失败trace保按需；关闭先子Agent/provider/MCP再等预热线程、释放所有索引，幂等不删持久数据。Semantic Insights复用当前root同代query/FTS/graph，不另索引；报告generation/上限/静态限制，分页不改范围，不因展示执行修改/验证。
- metrics只进程内task数量/errors/p50/p95/分类时长/重复尝试，无参数/输出/路径正文，无context归unscoped，不变调度；有界JSONL trace默认logs/runtime-trace.jsonl，CHAOS_DEBUG_TRACE=0关闭、CHAOS_DEBUG_TRACE_PATH指定。checkpoint事实仅Host固定八元数据，无revision/summary/source/messages正文，持久错误/取消传播。追加Skill/peer系统文本仅刷新本地prompt_estimated_tokens，不改检索/Harness。cost持久token无定价不估，doctor有界TCP不发key/HTTP。
- CLI help/version先于Application，ask/resume/JSON/task-resume显式--attach摄取、仅附件补默认prompt；--isolated显式作用域，--reclaim-workspaces单独报告不与位置参数混，重复两flag/未知mode拒；Ctrl+C130无traceback，provider配置安全具体原因/路径/下一步。ACP用官方SDK stdio、stdout仅JSON-RPC，无附件/位置参数/交互TUI审批、不复制loop；interactive bootstrap重依赖前临时扫描，初始化失败/成功恢复模式，help/JSON/ACP/管道无动画，旧启动器一致。
- UI唯一Muted Slate，宽屏追加/手机有界，mode≠permission，diff/rewind只读不改Git index；完整能力status，不接管终端设置。provider Markdown结果前置/短层级/有意义强调，不装饰虚构重要性。

## Units
- `ProjectMemoryControl` / `ProjectMemoryContextBuilder`：Host原项目固定scope、用户CRUD与≤4/1024tokens不可信预算前引用；scope拒绝仅清可选引用、不阻原任务，UI/CRUD仍拒越界；不打开来源、不复活撤回条目。
- Child composition：生产 RuntimeDispatcherFactory 在分配 Provider 前要求 typed 父 ActionExecutionContext、具体 task 和 TaskAuthorization，从持久父任务冻结授权、workspace root 和 owner scope；子上下文及动作使用同一根，角色权限仅收窄。子 token/tool 额度注册到持久共享父预算，不以 UI 当前 thread 猜归属。收尾在释放父 owner 和 pause checkpoint 前取消并等待真实子 runner/closer。S8 已将旧工厂独有保障迁移到生产入口测试，再删除无调用旧装配。
- `Application.tasks` / `AcpTaskService`：前者复用同一 Foreground 服务；后者只投影稳定 ACP Session 到明确选中的持久 Task，校验来源、thread relation 与 child binding，不替代 Task 生命周期。
- `ContextAssembly` / `ContextScopedDispatcher`：显式 builder/client/actions/snapshot；作用域内经原 dispatcher 及中央 policy 执行，异常、取消恢复作用域，不覆盖共享 Root。
- pending_action_recovery：操作者核对与gate内本地回执观察，不执行原动作。
- create_application/Host composition：共享依赖、方言/能力/权限/冻结runtime/入口组合。
- Root/TaskScoped/RestrictedDispatcher：纯观察解析与中央校验审批执行分开。子 RestrictedDispatcher 固定父授权，compact 展开及插件目标解析后按共享能力分类校验 write/execute/network/outside 上限；权限模式、永久进程规则和再次传入授权均不能扩大它；主 Dispatcher 保持既有审批语义。
- ManagedWorkspaceRuntime/MutationPool/SessionRouter：source/task身份、持久mutation/recovery与索引生命周期。
- RuntimeSelection/Provider/Auth controls：空闲原子替换、目录与冻结恢复。
- Foreground/Conversation/Project controls：同task/controller与消息分支，不复制执行状态。
- Context/Verification：冻结策略与最终请求额度；有界历史、可信身份、共享证据门。
- CLI/bootstrap/ACP：同应用契约与输出协议；CLI执行按TaskResult退出、成功查询0；child结果需明确终态，未知/取消不冒报完成，建议不作验证证据。
- Plugin/Peer/UI composition：受限贡献投影、Host-owned交互，不扩权。

## 参考
历史逐接口说明、CSV continuity分组和2026-09-05实验不作为默认生产策略。保留于对应评测/参考文档；其事实边界仍是：offline scripted output/usage不是API成绩/真实Provider证据，不代表完整TUI TaskRecord验收；阶段只完整tool组落盘后结束，固定profile/model/effort，unknown usage保预留，v2显式选择不改变v1默认budget/events，语义审阅未完成不称整链覆盖、不改生产memory策略。评测累计20轮/100工具为该评测值而非生产默认。上下文实验参考 `docs/context-boundary-experiment.md`、`docs/context-boundary-results.md`（相对仓库根）。


接口细节与历史说明（非默认规则）：`docs/next-version/s4/reference/host-before.md`。本文件已保留必要约束；参考快照不覆盖当前规则。
