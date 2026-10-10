# S10 Context consumers 实施与物证

默认 semantic 与冻结的 summary/boundary/persistent 已通过 Sessions 公共有界接口构建。默认无覆盖的超长日志明确失败，不全量加载或悄截尾；显式 compact_context 分批复用既有 SemanticCompactor/HandoffWriter，persistent 写既有空 carry 窗口。取消后保留真实已发布前缀，重开继续，全部原文和来源锚点保留。

## 验证与运行记录

Locked candidate Python 3.13，main PYTHONPATH。新数据库全部显式 TemporaryDirectory，Repository 前 assert 路径归属。没有真实 Provider HTTP、用户数据或运行用户 Host。PreparedProviderRequest fixture 使用测试 JSON 序列化，不能宣称真实 Provider usage。

- ThreadIntelligence 正式 46/46 PASS，19.147s，context-thread-final.log。
- 6000 条三窗口策略迁移 focused PASS，78.829s：首个 window 发布后取消，重开续进、原策略 build、Host2000 prepared 预检、全部原 payload digest 不变。boundary 实际第129窗可读取且恢复没有历史总128cap。
- 首轮低容量测试约150 CPU秒仍运行，经核实本次测试进程后主动停止，没有计入通过。每轮重读1000行造成额外成本；后来复用上一已准入前缀行数作读取提示，较大组逐级扩回1000，候选与发送仍各自 Guard 检查。
- 首轮完整迁移运行351.170s、三错误：summary尾组超Host1000；persistent固定GUIDANCE自身1076 UTF8字节，必要状态无法装入1000；boundary八字段输出超过128 handoff额度。成功fixture明确采用Host2000、handoff512，work6000不变。产品最终通过原builder及prepared Guard预检，尾部完整组必要时继续覆盖、最新真实user原文重新注入；prefix/user/carry仍不fit则失败，不返回假migrated。Host1000保留为不可恢复负向。
- Window正式首轮33项91.069s仅一failure：新增revision负向fixture未指定safety，默认16000超过API10000，尚未到revision断言；修成与成功fixture同safety20后精准复跑记录context-window-negative-repaired.log。最终重跑记录由context-window-final-repaired.log确认。

命令：scripts/run_test_suite.py --start-dir src/code_agent/thread_intelligence/tests；scripts/run_test_suite.py --start-dir src/code_agent/context_windows/tests；python -m unittest code_agent.thread_intelligence.tests.test_explicit_history_migration code_agent.context_windows.tests.test_explicit_history_migration。不自行冻结、全量候选或推送。

## 主要边界

每次原文物化不超过1000行/4MiB；完整工具组按exact ID+name闭合。合法digest但partial group、错name或nameless pending均不能覆盖；旧nameless原文仍可只读查询，无执行bypass。旧tool fixture补真实调用name，保留原断言。首次流式验证旧格式hash与首末anchors，message_epoch及完整metadata journal摘要允许append热复用，update/delete/fork/损坏重新验证拒绝。

129个obsolete checkpoint由SQL覆盖排除；确实必要的checkpoint上限128，选中metadata与raw suffix共用4MiB累计界限。窗口metadata每页16流式全journal遍历，仅保last2，没有历史总128限制；metadata SQL随窗口数增长，raw source不因LRU128淘汰而全扫。单组过大、source revision漂移、CAS冲突、无真实checkpoint或handoff容量失败，均保历史及已发布前缀。完成预检不发送main；compact_context没有tools参数，真实带工具的最终请求仍在发送边界由同Guard重检。

## 同fixture冷热基准

context-consumer-benchmark.json及context_consumer_benchmark.py。前提是有效checkpoint/window已覆盖除末尾两个完整组以外的来源；无覆盖6000旧日志另由迁移测试验证。每个tool正文4096字节，与history-baseline的编码数据不同，不能算跨fixture百分比降幅。

|策略/行数|冷decoded rows/bytes|冷SELECT/peak bytes/ms|热decoded rows/bytes|热SELECT/peak bytes/ms|
|---|---|---|---|---|
|semantic/600|601/868713|79/441988/148.69|7/8733|31/55068/37.34|
|semantic/6000|6001/8692713|415/521704/1130.0|7/8739|31/55066/39.46|
|persistent/600|601/868713|88/431178/148.8|7/8733|36/55255/44.05|
|persistent/6000|6001/8692713|424/527055/1188.47|7/8739|36/55501/56.22|

首次多读1行是单独保留最新真实user。peak为构建tracemalloc，不包含fixture准备，不等于进程RSS。JSON已核验为4项有效数组。

context-files.json列本Agent18个产品/测试/契约文件，不含docs脚本及其他Agent的client/counting/policy、Sessions/Core文件。等待Root合并冻结与独立监督。


新增监督反例已修：300行、每工具2000B原文，work100k/API120k但Host20k。三策略 compact→原策略build→Guard.preflight PASS（2.916s），最终prepared估计<=19980，window_input_cap和persistent remaining按有效Hostcap，全部source digest不变且History首条回读。正常 summary/boundary _rotate 先选择可准入完整prefix，cut以真实source条数推进，后续未覆盖tail保持；仍大于输入cap的尾部明确失败，显式compact持续分批推进。compact成功物化但prepared失败不能冒称可恢复。Root新增公开effective_input_cap，未改Root所拥有client。


最终 Window 正式34/34 PASS，94.792s，context-window-final-repaired.log；Thread46/46 PASS。最后源码修改后 Window 全套已验证，Python进程查询未发现仍在运行的本Agent测试/benchmark。已停止产品源码修改，等待Root冻结。
