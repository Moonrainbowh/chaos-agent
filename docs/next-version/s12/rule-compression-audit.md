# S12 Host/Remote 等义规则压缩审计

只改 `chaos_agent/AGENTS.md` 与 `chaos_agent/remote/AGENTS.md`；没有改代码、Root 或其他 Feature 契约。原文核对快照 `host-rules-before-compression.md` / `remote-rules-before-compression.md` 是非默认参考；强约束仍在当前契约，不以快照代替。

真实生产 `RuleLoader.load/render` 测量：Host完整链4919、Remote完整链5995 token；额度仍6000，总提示上限仍20000。Remote剩5 token空间，此结果只证明当前链可容纳，新增规则仍须重测。测量脚本不构造仓储/Provider。结果 `compressed-rule-chains.json`，精确两文件原始字节SHA256在 `compressed-rule-files.json`。

## Host 逐项映射

旧边界的21条与新边界保持同序；标题/连接词/重复职责压缩，句中约束与数值保留。

| 旧条目 | 当前位置与保留约束 |
|---|---|
| H1 S9验证 | 边界1+13：风险适配、可信LOW/TRIVIAL、相关/全tests、关键build、模型/exit0非证据、兼容禁用unverified、无需修改显式接受 |
| H2生命周期 | 边界2+17，Foreground Unit：所有入口共享；Session/Task/Attempt区别、冻结权限/runtime、预算不重建、终态新Task/未终态原恢复、Workflow仅事实 |
| H3上下文/旧工厂 | 边界3+Context/Child Units：显式builder/client/actions/snapshot、禁猜_inner、只读入口无需重依赖、迁真实测试/核引用再删旧、保安全职责 |
| H4恢复核对 | 边界4+12：操作者非模型tool、gate内completed/task/call/owner/物理workspace/hash、PREPARED/GAP/未知不猜、人工非evidence、禁旧ID重放 |
| H5工具/插件 | 边界5：工具默认集/按需web/契约发现、权限先compact、原名仅披露operation、Host target/callID、观察不建workspace、qualified执行及双policy、MCP opaque/不信模型risk-progress、typed schema/preflight/中央审批、外路径准入、Web关闭且不授网络 |
| H6主子/peer | 边界6+Child/Restricted/Context Units：single/team/child区别、rename不暴露、loader不扩权、角色profile/预算/取消/单写者/冻结快照、advisory非证据、可信manifest/不可变贡献、peer不授权/不泄正文、task-owned/taskless区别、输入不可信/共享activity lock |
| H7进程平台 | 边界7：Provider前冻结方言、source/worktree/主子/切换共能力、prompt不泄exe、status可核、故障真实、PS Stop/native bytes、program+args无shell/env/stdin及拒launcher/cmd/bat/NUL/超长、独立解码截断/Base64/codepage、legacy边界、长路径前置可见 |
| H8检索 | 边界8：1–16、device64/file128 unsigned、generation/signatures/读前后核、stale无部分源码、提示合并/新信息再批、Git inventory/ignore/保护递归、25/50/cursor、搜索限域/timeout部分标记/result_limit、不把部分无命中当全、deadline/Git失败无全盘fallback/留验证预算 |
| H9隔离 | 边界9：默认source、无原因无Git/dirty/copy、direct/managed/非Git原因、auto触发、未知拒、ContextVar/reset、required前置/already-active/失败不回source、单写者/唯一dirty seeding/敏感路径opt-in共享Guard |
| H10存储 | 边界10：新短根/API保护/snapshot只新根、旧missing-only只读且禁迁移回填GC删除、中间目录限制/任务根可用、无daemon/自动提交、失败补偿/interrupted、local source=worktree/不复制/≤4Git、metadata-only与其他SQLite错误区分 |
| H11回收 | 边界11：显式可证明退役、live/pending/protected/missing/nonterminal保留、branch/HEAD祖先/source_head/无自有提交/tip/干净、失败保留用户文件、marker512浅层、Home/根轻量 |
| H12mutation/rewind | 边界12：canonical独立对象、source/task不共gate/snapshot/plan但唯一Sessions、router回退/所选checkpoint、PRE/POST先写/未知gap、owned恢复后释放/foreign阻写、batch先rewind/锁内重读/不缓存跳冲突/并发一次、缺目录区别、双观测只读/禁apply等 |
| H13编辑/验证 | 边界13：计划ID/digest/一次消费/重preflight、ignored/untracked风险/取消顺序、回滚拒绝不伪变更、模型risk/Diff不可信、活动Context同代索引/root/scope、仅修改刷新、structured默认/显式禁用保tracking与命令但unverified、未跑非通过 |
| H14runtime | 边界14：三轴/四档/真实profile/统一tools/Ultra硬预算、缺profile拒、原子切换失败保旧、legacy/plugin不重置、冻结完整ModeSnapshot/恢复漂移/不回宽、BaseException主子close/不盖原错、≤300/TUI兼容、Anthropic prompt-only/None/拒非默认/wire映射 |
| H15附件/模型目录 | 边界15：共享显式input modalities/动态profile/单参数factory、不解析blob猜能力、Responses/Chat发现缓存确定性profile、继承配置、失败保旧/发现不切或写默认/恢复原配置 |
| H16认证 | 边界16：隐藏密钥/码、平台auth槽影响、原子保存/刷新区别、WorkBuddy无种子显式加载及缓存重登失败、Antigravity本地二级无网、login/task选择目录恢复不写默认、CLI前置/显式auth/ProviderConfig验证/不替旧不因刷新推理、TUI三元组过期回默认/tuple非profile |
| H17手机/分支/命令 | 边界17：无需cd模型、显式root切换关旧/失败回列表/compact、节点/runtime/完整前缀/不复制状态预算恢复文件、终态先模型再消息新Task/禁跨项目、!origin不可信无模型/!!不入库/取消真实结果 |
| H18context/memory/索引 | 边界18+Context Unit：冻结summary/boundary/persistent+budget/no HandoffWriter、原根lineage scope/CRUD-CAS/active适用/预算前不可信、外scope拒/user关/不扩权证据另库、实际tools、后台预热失败trace/按需、关闭顺序幂等不删数据、同代Insights/限制分页/不触发修改验证 |
| H19遥测 | 边界19：进程内纯计数/延迟/重试、无正文/无context unscoped/调度不变、有界trace默认与开关、固定八checkpoint元数据/无正文/错误取消传播、估计仅本地/检索Harness不变、cost仅持久token无价不估、doctor有界TCP无key/HTTP |
| H20入口 | 边界20+CLI Unit：help/version前置、attach/默认prompt/isolated/reclaim/重复与未知拒、Ctrl+C130/安全诊断、官方ACP stdio/JSONRPC/无附件位置或TUI审批/不复制loop、bootstrap临时扫描恢复/无动画/旧入口一致、TaskResult退出查询0/child终态 |
| H21UI | 边界21：唯一主题/宽追加手机有界/mode非permission、diff/rewind只读index、完整status/不接管终端、Markdown实际信息层级非虚构强调 |

旧13个 Units 合并为7组，未删除主要约束：ProjectMemory→Context Unit保≤4/1024tokens及scope失败降可选引用、CRUD仍拒、不打开来源/复活；Child→Child保Provider前typed父上下文/具体Task/授权、同根/owner/持久父预算、父释放前等待真实child；Application.tasks/ACP与Foreground合并，保稳定Session/来源/thread relation/child binding；ContextAssembly显式字段在边界3，作用域恢复不盖Root在Unit；pending_action_recovery/ManagedWorkspace/MutationPool/SessionRouter合并，具体安全约束边界4/12；RuntimeSelection并入入口Unit，Provider/Auth/Attachment目录能力与原子恢复在边界14–16；CLI/bootstrap协议在边界20和Unit；Plugin/Peer/UI受限Host贡献在Context Unit及边界5/6/21。

参考事实边界仍默认可见：offline结果不当API/Provider/完整TUI证据、完整tool组落盘/固定profile-model-effort、unknown usage保预留、v2显式不改v1 budget/events、未审完整语义不夸整链或改生产memory、20轮/100工具仅历史评测。只压缩表述与链接格式。

## Remote 逐项映射

| 原边界/Unit | 当前保留位置 |
|---|---|
| 一次性配对/单设备/单Task/HTTP-WS生命周期/复用Application-Foreground-Sessions与不负责项 | 边界1、Pairing/Task/create_host_app Units |
| 最近项目/会话/搜索分页/source归属/未知未归类不隐藏 | 边界2、Catalog Unit |
| 已知项目选建/原契约恢复/终态新Task保旧、刷新重启持久历史、窄单栏固定输入 | 边界2、Applications/Task/PWA Units |
| 固定用户SSH ProjectStore/无历史登记、认证添加浏览最近移除、登记与归属分离/不删工程历史、目录只读不模型/切Task | 边界3、Catalog/PWA Units |
| 手机导航/大按钮/仅user深绿/会话隔离草稿导航不丢 | PWA Unit保64×1024、无会话按项目、发送成功才清该条 |
| S12共享中央/绑定task-action-workspace-version-owner/TTL、拒过期重放撤销、未知S5不由继续批准 | 边界4、RemoteRequestControl Unit；补未Task Skill/插件本地和审批不扩冻结权限 |
| HTTPS/WSS默认/loopback上游/明确私网IPv4调试/不改防火墙装服务/撤销不回滚 | 边界5、Transport；证书加载非真机信任链验收保留 |
| Pairing存储摘要/StoreLock/原子fsync/保存成功消费/读取观察外撤销/损坏缺失拒 | Pairing Unit；新增authorized_response持锁重认证贯穿CAS/唤醒、取消取锁worker必须等待释放 |
| Catalog registered/recent/公开页/登记最近checkpoint/无Provider/移除不隐藏seed守移除/串行thread外/缺归属目录历史只读 | Catalog Unit |
| 一个备用app惰性生命周期/共享库当前模型/恢复原约 | Applications Unit |
| 单槽/有界每run事件/公开原子快照cursor/固定run/断线不取消 | Task Unit；新增host_epoch/gap reset回持久snapshot、旧cursor不重放错run |
| 事件有界且不泄reasoning/凭据/context/原异常正文 | EventAdapter Unit |
| HTTP/WS/static组合可注入ProjectStore、认证前置/选择不替活动app/目录有界浅层/WS发送等待核撤销/ws源location/401或4401才清凭据 | PWA Unit |
| 导航/确认/新建搜索分页刷新、气泡分段、文本节点Markdown禁模型HTML、旧会话和关闭目录迟到结果过滤 | PWA Unit；新增gap/epoch先持久快照与卡再低cursor、明确卡状态、离线旧卡不自动批准 |

Remote保持8个主要 Units；新增RequestControl只中央查询/响应与最小决定，不引入第二授权引擎/通用shell/新账户或Relay。两份当前规则均未改变实现行为与预算。
