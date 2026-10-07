# P4 harness review notes

状态：脚本准备与纯离线 selfcheck 完成；**未 prepare owned、未启动 Host、未执行公开预检、未 HTTP、未 Provider**。P3完成及Root最终commit后，才由Root运行prepare/preflight，独审通过后execute一次。脚本不标质量PASS。

## 新文件

- `run_real_source_completion.py`：默认selfcheck；prepare冻结源码HEAD、clean production（含untracked）、脚本/plan/portable fixture hashes、owned配置/plugin hashes、来源与共享规则hash。
- `p4-origin.json`：实际v7 case、原worker-result原始SHA/关键结果/child记录，旧v2 unknown 80703和v6 unknown 95586只引用、不重结算。原acceptance-matrix引用保留。原owned与v7脚本未修改。
- `p4-selfcheck.json`：本次真实命令输出。

## 固定边界与执行路径

父输入≤512，明确委派一次300k/5tools/240s，并将同四路径声明为required_sources；父核对责任与子读取目标分离，shared AGENTS无父委派职责，无行为结论或正确行号金答案。来源通过P2portable base64原bytes恢复；GLM5.3-flash/medium父子、父1m、prompt300k/schema20k/output4096、12rounds/40tools/8per-round、transport60s/2retries，父watchdog880/总900不改。

preflight以真实配置、public tasks.start→events→原dispatcher/factory/context/engine/ChildResult运行，仅外部AsyncClient.send返回Chat SSE fake。父fake披露delegate、委派、独立四read、答复；子fake直接四read再答复，不冗余load read。额外请求显式失败。预检断言actual delegate schema optional required_sources、child持久binding、实际child首请求角色/read schema、续请求四来源text、真实成功tool/ChildResult、父持久analyze和预算。预检没有模型语义证明。

execute校验同freeze与preflight状态，exclusive execution-started marker限制单次。Windows Job在释放worker前绑定，总900包含cleanup；父880cancel走production。外部send审计记录每次transport尝试（包含重试），保存exact request bytes/hash，无headers/URL；凭据入body则写redacted证据并终止。预检和live wire前缀隔离。原始事件/回答/messages、usage/request/window、child events/bindings/lifecycle、owned数据库保留。pending/unknown不会变零或重新结算。异常只记录error_type，不输出异常文本潜在凭据；durable数据库保留，禁止自动恢复重试。

## 待P3接口稳定后核验

当前实现按child_budget flat required_sources读取；P3 agent已告知该拟定接口。`source_completion`记录kind是待确认观测点，尚不能当来源事实；最终notice/baseline键需P3反馈后调整。真实source gate证据来自actual child成功read与实际wire正文，不来自metadata或预检fake本身。P3空答复约束也须以真实独审为准。

脚本尚未运行Host，因此Chat SSE兼容、生产schema/binding以及端到端断言仍待零HTTP公开预检发现问题；当前compile/selfcheck不能宣称该预检通过。Root最终freeze前可修订脚本，freeze后脚本改动需要新owned。

## 外部独审判据

Root从真实请求、原回答、events、数据库逐项判断：四份child完整来源覆盖；current/legacy分析；五行为及测试映射；物理path:line；明确未运行测试/unverified；父analyze与child终态；Provider COMPLETED与engine COMPLETED分别核查；当前usage唯一结算完整，旧unknown单列。来源齐但答复只有计划或空答复仍FAIL，退出0/ATTEMPT_COMPLETED不等于PASS。新候选五平台CI需对应最终新SHA，不能借旧afbd37578152123。一次成功仅此有界门，不扩展一般成功率。

## 实际命令

在新worktree运行 `.venv/Scripts/python.exe -m py_compile docs/next-version/s16-source-completion/run_real_source_completion.py`，1文件通过；运行同脚本`selfcheck`，四来源hash、父长度、synthetic凭据审计通过，Host/Provider计数0；`git diff --check`通过。未运行任何prepare/preflight/execute命令。首次父prompt超512的本地纯selfcheck失败已修短，未放宽≤512断言。

## TEAM自然租约补充

Root已放行P3 agent最小typed TEAM租约修复：explicit deep优先；TaskContract.agent_topology==team自然STANDARD；single/legacy analysis仍QUICK；旧已持久budget不迁移。harness不修改或续租budget。首个parent context_built观测必须为STANDARD、初始12model turns/30tools、renewals0，hard limits仍12/40（原v7同为STANDARD12/30、0renew）。该实际budget快照保存在worker result与context-budget-observations.jsonl。

预检及live均断言父最终持久消息为非空无tool_calls的assistant，并至少有3次实际parent请求（披露、委派、最终请求）；请求身份来自原BudgetedWindowClient.current_thread绑定，不凭prompt猜测。保存final_request_body_file及消息位置。child完成或sources齐不能替代parent交付；这些结构断言不证明五行为语义，仍须独审读原答复。离线fake父4请求、子2请求会逐次走自然租约，若生产仍QUICK并缺父最终交付则预检应真实失败。

本次仅更新docs脚本及说明，再跑1文件py_compile、pure selfcheck、git diff --check；未prepare、未Host/HTTP/Provider。

## Child进入前冻结验收护栏

P4 harness原来仅在完成后核binding；现增加preflight/live共用的EngineChildRunner.run observer，在调用original之前核agent s16.sourceaudit、300000/5/240、规范化四来源集合且无重复（允许同集合顺序变化、./、路径分隔符等价形式；不允许绝对路径或..）、且首次/唯一child进入。遗漏required_sources、路径漂移、agent/budget漂移或第二child均立即AssertionError拒绝，原runner尚未创建child/thread/context或Provider请求。observer只断言/记观测后调用original，不补参数、不替代factory/engine/模型，generic生产delegate optional语义未修改。worker结果保存child_entry_observations。

pure selfcheck包含正确冻结参数1正例，以及遗漏、部分、错误来源、错agent、token/tool/time漂移、第二child共9拒绝反例；无Host或HTTP。完成后仍保留真实binding/wire/工具断言及独审，不能把入口护栏当来源完成证明。

Root补充集合语义已同步：入口与完成后binding断言均按规范化集合+无重复，不要求fixture列表顺序。pure selfcheck增加逆序/./ /backslash正例，以及规范化后重复反例；仅核验，不修改实际request参数。

## Persistent wire后缀核验

核查实际persistent_builder.py：消息content在原正文后拼接且仅拼接一个`\n[history_ref window=<32 lowercase hex> item=<32 lowercase hex>]`；两个ID分别是window与稳定item UUID hex。新增wire工具解析使用JSONDecoder.raw_decode严格读取JSON object前缀，并对剩余tail fullmatch真实格式；无tail亦可。禁止strip/rstrip或吞坏尾巴。原JSON source.text内任意history_ref样式文字保持原样。durable message解析仍json.loads。

同时避免persistent user suffix导致child误识别：离线fake及wire子请求筛选使用实际BudgetedWindowClient线程绑定，而非user objective逐字相等。pure selfcheck含纯JSON/真实合法suffix2正例、6坏suffix拒绝，及原正文含marker保留。1文件compile、selfcheck、diffcheck通过；未prepare/Host/HTTP/Provider。

## P3最终接口同步

P3 agent已反馈：delegate.required_sources optional list[str]（≤32、每path≤1024）；child_budget平面payload.required_sources为canonical list。纠正记录kind=`source_correction`、payload.completed为已满足路径；记录id及developer notice/baseline同事务；无另存proof表。harness采集kind已从待确认source_completion改为source_correction，完整child events/messages可核对notice。失败投影source_requirements_unmet/remaining，来源齐但空答复empty_summary；真假语义仍独审。仅接口采集名更新，无Host/HTTP/Provider。
