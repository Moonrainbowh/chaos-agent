# S10 独立监督：冻结前发现

当前仅审查主工作区，不代表 candidate 或阶段 PASS。监督者未实现产品代码。数据库只在本次显式 TemporaryDirectory 中创建，构造仓储/重开及直接 SQLite 前验证 resolved path 在该目录；模型测试仅 httpx.MockTransport，没有真实 Host/Provider 或默认用户历史库访问。

阻断性产品反例：原 ToolGroupCursor 只核 tool_call_id，错误 tool name 仍闭合；Sessions pending gate 同时要求 ID+name，因此合法 digest 的 semantic checkpoint 和 window 可以隐藏实际未配对动作。独立 2 FAIL 见 supervision-initial-failure.log。主实施者已修 exact name，后续独立探测两项通过。未删除初始日志。

兼容缺口：无既有 checkpoint/window 的正常旧长记录超过新 1000 行/4MiB materialization 限额时，正常 build 明确失败，保留原日志，这比静默截尾正确。但仅有错误中的 migration decision 并不是可执行恢复路径。现有 WindowContextBuilder.compact_context 仅排队 request，下一 build 在读取 request 或进入 rotate/reset 前就因 materialization 超限失败。已要求 semantic 和 summary/boundary/persistent 各自沿现有策略的显式有界完整组迁移，不允许默认 build 隐式迁移、变更旧策略或覆盖原文。

独立物证（冻结前主源码）：supervision-counterexamples.py 前 6 项 16.400s PASS；6000 来源首次 6001 条解码，新增 suffix 后 5 条，重开 6002 条，原 user、稳定旧 UUID 和 Unicode fragment 一致。实际 Chat Unicode arguments/schema 最终 prepared 超限时零 reserve/HTTP。partial usage 22 tokens 是 fake usage 事实，另有未知预留，重开不双计数。

扩展首轮 9 项：8 PASS、1 监督 fixture ERROR，原因是监督者猜了不存在的 EventKind.MODEL_TEXT_DELTA。读取真实 events.py 后改为 MODEL_EVENT，单项 1 PASS；原 supervision-main-expanded.log 保留。新增实际范围为旧三策略保存窗口重开/漂移拒绝、热 progress 并发同序号正文修改失效，以及同序号 event 修改后的 recovery version 漂移。

最终必须候选物理冻结、标准全量及独立统一重跑通过，才能给 S10 PASS；当前不开展 S11。

后续实际产品反例与修复探测：

- 17MiB 损坏 history_progress payload：读取没有 SQL 长度预检，试图 json.loads，独立 1 FAIL。加读取侧长度预检后独立 1 PASS（0.197s）。supervision-cache-initial-failure.log / supervision-cache-repaired.log 保留。
- 1101 条旧完整调用日志（三策略）显式 compact 后仍因原始 materialization 限额不能 build，1 test / 3 subtest ERROR；补显式逐组有界 window migration 后三策略均恢复、保持原 strategy/原文和 user。persistent 零摘要，另两条沿真实 HandoffWriter。
- latest task result 的 subject=None：SQL 等号绑定 NULL 永不匹配，50 条当前身份 result 全被丢掉，独立 1 FAIL。改 NULL-safe 身份查询后，50 result + 100无关事件仍只解码32 result和1 latest RUNNING。与三策略迁移合计2 PASS（3.701s），初始和修复日志均保留。
- semantic 1101 条显式迁移第二次摘要时取消：第一次 durable checkpoint 留存，仓储重开再 compact，第二个 checkpoint 发布、首次 id 保留，原文数量不变、原 user 可追溯，独立1 PASS（0.915s）。首次监督 fixture 漏 ContextConfig cwd/system_prompt，TypeError 日志保留；读取真实签名后修正，仅是 fixture 问题。

以上仍是主工作区冻结前探测。窗口迁移最大128条 metadata 与 semantic 至多128个受限 summary 是明确资源界限；4MiB 只指原始 suffix materialization，不代表所有派生 metadata 的总内存。较小 final request capacity 导致 migration 分过多窗口时，必须报未完成并保留已发布 prefix，不能用成功迁移回执遮盖恢复上限。

最新冻结前补充（独立脚本现19项，仍 CHANGES_REQUESTED/待冻结，不是阶段 PASS）：

- SQL conversation usage 对 truthy invalid tool_call 字符串误作空对象，覆盖有效 Usage，与原 UsageAccumulator 不一致；独立1 FAIL 后修复，最新单项 PASS。保留 supervision-ui-and-usage-initial.log 和 supervision-latest-repaired.log。
- 有界 UI restore 在600消息/160后续telemetry下保持完整 accepted_partial TaskResult、状态与 strict exit code；新增 Mobile history APIs 在读取body前拒绝foreign项目。独立探测通过。
- /cost 禁止全量load_events/load_conversation_events时仍从公共SQL聚合给出fake usage总量10/cache2，1 PASS；实际 RuntimeContextFactory 冷compact首次调用即建立guard binding，收费辅助请求后恢复未绑定，异常也reset，1 PASS。
- 低于1000行仍有大原文的旧策略不能用较大API/work预算替代Host cap。81行/40完整组/每结果1024B，API200k/work150k/Host5000，summary和persistent显式compact后actual Guard.preflight均通过；cap/remaining<=4936，summary仅辅助HTTP，persistent零HTTP，原81行与必要user保留，supervision-small-row-corrected.log。persistent消息附加history_ref，监督首次全content等值比较误判1FAIL，已读取真实实现后改为核原文前缀和引用标记；初始日志保留。

正式最终要求仍为冻结candidate源文件逐项hash、30套外层成功摘要和19项独立candidate统一重跑。此前主源码探测不能替代这些物证。

首次正式冻结结果：83路径 main/candidate raw SHA 与snapshot相符，patch e0bebf34e37886955701b1aa417630c1dede4607c52f20a6457eedd57c231df9。补独立持久化 context_selection 同策略 output cap 漂移及旧无facts不补造后，candidate20项统一30.094s PASS。第一次19项出现旧错误文案断言不匹配1FAIL，已按新typed显式迁移错误修监督fixture，日志保留。

标准全量首次 outer summary 为30 suites/3284 run/5 errors/0 failures/30 skips/0 unrun，阶段仍 CHANGES_REQUESTED。5 ERROR：Host progress 两项 SessionPersistenceError、一项 excluded-directory trace final input cap；另两个 oversized prefix 测试在 assertRaises 外 build 已明确拒绝，需修fixture验证位置，仍必须零HTTP。物证保存 supervision-standard-initial-failure.log。不得用20项独立PASS掩盖标准全量失败；修复、重新冻结和最终验证后才可放行。
