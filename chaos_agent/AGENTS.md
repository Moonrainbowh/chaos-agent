# Host
Windows/Linux/macOS共享TUI/CLI/JSON Host，发布chaos-agent保agent/本地状态。仅组合依赖/typed actions，禁重写policy/workspace/runtime安全或Feature行为、另建loop/索引/runtime、自动翻Shell/初始化Git/伪报成功。

## 边界
- S9默认风险验证：可信LOW/TRIVIAL轻量，其余相关/全tests，关键tests+build；模型正文/普通exit0非证据，无需修改由操作者接受。
- 各入口共Foreground启停/恢复/决定/结果。Session持久消息树，Task冻结目标/授权/runtime，Attempt执行实例不重建预算；终态继承消息建新Task，未终态原恢复门，Workflow仅事实。
- Context显式builder/guarded client/actions/snapshot，不猜_inner；task list/result/recovery走仓储无需Provider/Shell/plugins/UI。替代工厂迁真实入口测试、核引用再清旧，保安全职责。
- recovery/resolve仅操作者非模型tool；gate内核completed回执/task/call/owner/物理workspace fingerprint/当前hash；PREPARED/GAP/外部未知不猜成功，人工报告非evidence，不重放旧ID。
- 默认主工具read/search/write/edit/execute，按需web/load_tool_contract；先mode/role过滤再compact，原名兼容只限披露operation。纯观察compact后按Host当前plugin target映射保call ID，不建workspace服务；执行qualified插件名、plugin/target分别风险policy。MCP/未知opaque，模型risk/progress不可信。副作用前typed递归schema/preflight/中央policy/审批，再Workspace/Runtime，仅准外部路径进Unit。WebAccess source/task共关闭HTTP，启用不授网络权。
- single/team主可已注册list/send，delegate仅team；child禁delegate/peer，rename不暴露模型，loader不扩权。child按角色profile守预算/取消/单写者/冻结快照，advisory非evidence；plugin仅可信manifest/不可变贡献。peer不授权/泄正文，task-owned不后台恢复，taskless仅list/send；held/queued不可信，退避共runtime/peer activity lock。
- Provider副作用前探测冻结PowerShell/POSIX方言；source/worktree/主子/切换共capability配置。prompt不泄exe全路径，status可完整核验；方言不符/非零/Git故障如实结构化失败。PowerShell默认Stop/native原始字节；program+args无shell/env/stdin，拒shell launcher/.cmd/.bat/NUL/Windows命令行超限。stdout/stderr各严格解码/截断，不能解码给完整Base64/codepage；legacy script仅旧Runtime，file auto不猜legacy codepage。Windows长路径预算先resolve/mkdir/进程，启动/status/prompt可见，不以missing掩策略。
- generation-bound slices批1–16，device64/file128 unsigned不截断；当前RepoIndex generation/signatures+Workspace读前后核，stale只stale_repo_context无源码。提示合并已知目标/新信息再批。Git根tracked+nonignored-untracked inventory经Workspace过滤/子gitignore，子/普通目录保护递归。list默认25/max50/next_cursor；search限root/include_globs/max_results，timeout保matches且complete=false/incomplete_reason=timeout，result_limit标截断，部分无命中非完整。Git枚举计deadline，失败不全盘fallback；留修复验证预算查邻近行为。
- workspace默认auto/direct source，无隔离原因不探Git/dirty/复制worktree；direct恒source，managed独立Git worktree，非Git明确本地降级原因；auto仅并发写者/background/explicit需managed，未知值拒。ContextVar只声明本次原因、边界reset不猜Git；required isolation无法满足先于thread/task拒，保already-active原因，Git创建失败不静默回source。同source一本地写者；isolated唯一dirty seeding，本地不走；敏感路径独立opt-in，source/task Guard共享。
- managed新短根LOCALAPPDATA/chaos-agent-workspaces并列受保护API配置，snapshot只写新根。旧managed-workspaces/snapshots构造验证/missing-only只读fallback，禁迁移/回填/GC/删旧根/worktree；storage/repo中间目录禁workspace，worktrees/repo/lineage任务根可用。无daemon/自动提交；准备/绑定失败补偿，已建task interrupted可恢复；local lineage source=worktree，不建分支目录/复制，≤4常数Git；缺事实/旧direct占用metadata-only不阻启动，其余SQLite故障报错。
- reclaim仅显式退役可证无任务的注册worktree：live lineage/pending rewind/保护snapshot(rewindable=False)/缺失或未终态task均保留；branch须task_branch_name，HEAD祖先source_head无自有提交，tip一致/干净；任失败停止保留，不当用户文件垃圾。marker只直属≤512、不递归；Home/磁盘根即Git仍轻量。
- canonical root各复用editor/snapshot/mutation gate/计划/capture/CoordinatedSession；source/task不共享gate/snapshot/plan，facade共唯一Sessions。SessionRouter按thread/owner root，未绑child回owner再source，仅选facade写checkpoint。首次写前PRE/POST durable journal，未知写者先gap；异常/取消owned-only恢复再释gate，foreign conflict阻后写。启动batch恢复先rewind，查询后加锁重读，不缓存/跳conflicted，失败闭合/并发一次；不存在task/thread目录可跳，source缺失仍校验不吞。RewindRuntime双观测只读，无apply/restore/approval/provider/Git reset。
- edit Host不可变ID/digest，plan/apply分离一次消费，apply重preflight，ignored/untracked既有文件显式风险，取消先applying；回滚/预检冲突/拒绝不伪变化，模型risk/Diff不可信。verification source/task用活动Context同代RepoIndex不另索引/信模型scope，root漂移闭合；只修改意图/实际变更刷新，问答不跑无关验证。默认structured；CHAOS_STRUCTURED_VERIFICATION=0隐藏/拒run_verification但保tracking/普通命令，交付unverified；未执行不报通过。
- topology/profile/effort独立；四档mode绑定真实profile/统一tools/Ultra硬预算/策略，缺profile拒。空闲原子换provider/runner/dispatcher/audit，失败保旧；legacy/plugin只投影不重置三轴，恢复model/protocol/host/digest漂移闭合。Task冻结完整ModeSnapshot/实际tools，plugin恢复不回宽内置。任BaseException清partial provider：主best-effort close，同步child保调度async close，不盖原错；拆分Unit≤300，TUI兼容profiles/list_profiles。Anthropic medium仅prompt，wire确认前None；拒非默认effort及其切入，不猜字段，max_output_tokens→max_tokens。
- AttachmentStore/Ingestor+profile显式input modalities共注入主/子/切换provider，memory读动态profile；兼容单参数factory，不解析blob/猜能力。ApiModelControl显式Responses/Chat发现/缓存/确定性内存profile，继承protocol/auth/capacity/modality/budget；失败保旧，发现不切/写默认，恢复原配置。
- Authentication藏key/授权码，按平台/auth槽核影响，保存开始等原子结果，刷新失败非登录失败。WorkBuddy无离线种子可统一model显式加载，auth缓存/重登清槽/失败保登录；Antigravity首层仅本地二级不联网。login profile按同名平台/auth槽/目录恢复task及上次选择，不写默认；auth CLI先模型/workspace初始化，profile显式auth，追加前ProviderConfig验证，不替旧profile/default、不因刷新推理。TUI保存模型按(name,model,protocol)核验，过期回默认，tuple非ModelProfile。
- 手机项目/历史无cd/模型；切项目关旧app按显式root重建、失败回列表，mobile显式compact。分支核节点/冻结runtime/完整消息前缀，不复制task状态/budget/恢复文件；非终态原恢复，终态恢复模型后继承消息新Task，不继承执行/预算，禁跨项目。!command记origin/不可信结果/无模型，!!不入库，取消持久真实结果。
- managed context按冻结profile组合summary/boundary/persistent+统一budget client，persistent无HandoffWriter。Memory按Host原根/lineage固定scope、显式CRUD/CAS、active适用查询、预算前不可信投影；拒外项目thread/ID，user关闭，不扩权/验证/另库，模式只实际tools。RepoIndex后台预热不阻启动，失败trace/按需；关闭先子Agent/provider/MCP再等预热/释全部索引，幂等不删数据。Insights共root同代query/FTS/graph不另索引，报generation/上限/静态限制；分页不扩范围，展示不修改/验证。
- metrics仅进程task数/errors/p50/p95/分类时长/重试，无参数/输出/路径正文，缺context为unscoped、不变调度。trace有界JSONL默认logs/runtime-trace.jsonl，CHAOS_DEBUG_TRACE=0关/CHAOS_DEBUG_TRACE_PATH指定；checkpoint只Host八元数据，无revision/summary/source/messages正文，持久错误/取消传播。Skill/peer文本仅本地prompt_estimated_tokens，不改检索/Harness；cost持久token无定价不估，doctor有界TCP无key/HTTP。
- CLI help/version先Application，ask/resume/JSON/task-resume显式--attach，仅附件补默认prompt；--isolated显式scope，--reclaim-workspaces单独不混位置参数，两flag重复/未知mode拒。Ctrl+C130无traceback，Provider报安全原因/路径/下一步。ACP官方SDK stdio/stdout仅JSON-RPC，无附件/位置参数/TUI审批，不复制loop；interactive重依赖前临时扫描、成败恢复模式，help/JSON/ACP/管道无动画，旧启动器一致。
- UI唯一Muted Slate，宽追加/手机有界，mode≠permission，diff/rewind只读不改index；完整能力status，不接管终端设置。Provider Markdown结果前置/短层级/有意义强调，不装饰虚构重要性。

## Units
- `ChildSourceCompletion`：将真实子线程成对的 built-in read_file/compact read(file) 完整文本回执适配为 typed source snapshot；插件/MCP同名、模型 metadata、失败/截断/slices/父读取不算。完整历史按有界页与增量 cursor/epoch 核对，恢复重建事实，纠正要求不能从 context tail 或正文猜测；不新增 child 跨 owner 重启能力。
- 来源型 ChildResult 仅取最后非空无工具 assistant 答复，避免纠正前计划挤掉最终交付；缺省任务保持历史聚合，建议不成为验证 evidence。
- 工具披露说明区分模型请求与用户消息：当前已有完整 schema 的工具直接可用；缺失能力成功披露后供下一模型请求使用。仅提示措辞，不变 availability=next_model_turn、digest 或权限检查。
- Child role context：原 child factory 将 AgentDefinition.instructions 作为显式只属于该 child 的参数传入 ContextConfig；真实固定上下文预算前渲染，不放进 objective/user 历史；首轮、工具续轮和上下文重建沿用配置。角色文本不改变父冻结权限、工具筛选或验证事实。
- create_application/CLI/RuntimeSelection：共享入口/切换恢复；CLI按TaskResult退出/查询0，child须终态，未知/取消非完成、建议非evidence。
- Child composition：分配Provider前须typed父ActionExecutionContext/task/TaskAuthorization，持久父冻结授权/root/owner，child同根/角色收窄/父共享token-tool预算不猜UIthread；生产首次task-owned supervisor从持久冻结TaskBudget派生token/tool上限，显式ParentBudget仅收紧；同Task release/resume保留累计ledger及unknown预留，不因profile切换扩额，runtime关闭清缓存；taskless维持原默认。父owner/checkpoint释放前取消等待真实runner/closer；S8已迁生产测试才删旧装配。
- ContextAssembly/ContextScopedDispatcher：经原dispatcher/policy，异常取消恢复scope不盖Root。
- Root/TaskScoped/RestrictedDispatcher：child按父冻结write/execute/network/outside上限核compact/target；permission mode/永久进程规则/再次授权不扩，主审批原义。
- Workspace/Mutation/SessionRouter/recovery：身份/日志/恢复/索引。
- Foreground/Conversation/Project/Application.tasks/AcpTaskService：共享Task/分支；ACP核来源/thread relation/child binding，稳定Session映所选Task不替生命周期。
- Context/Verification/ProjectMemoryControl/ProjectMemoryContextBuilder：冻结上下文/证据门；不可信引用≤4/1024tokens，scope拒只清可选引用不阻Task，UI/CRUD越界仍拒，不开来源/复活撤回；Plugin/Peer/UI属Host受限贡献/交互不扩权。

## 参考
历史接口/CSV continuity/2026-09-05实验非默认；offline output/usage非API/Provider/完整TUI证据。完整tool组落盘才结束，固定profile/model/effort，unknown usage保预留；v2显式不改v1 budget/events；语义未审完不称整链覆盖/改生产memory；20轮/100工具仅评测。仓库根参考docs/context-boundary-experiment.md、docs/context-boundary-results.md、docs/next-version/s4/reference/host-before.md不覆盖当前规则。
