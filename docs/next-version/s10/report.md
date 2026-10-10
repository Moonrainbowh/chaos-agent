# S10 阶段报告

状态：DONE；新候选标准全量与独立终审PASS，允许串行进入S11。

基线是 S9 已放行 tree 1161bad09f69f1517df6e55763f6141fdeb5178b；冻结85路径，implementation.patch SHA256 5a4fc346668816db3939b5c8e61ff9dfcf7c1f906fbe855ccfd814f36ace059a，snapshot.json记录每个原始文件SHA256。隔离候选使用锁定CPython3.13.2和既有依赖。冻结使用临时Git index；没有提交、推送、运行用户Host、修改原authentication或默认用户库。首次diff check拒绝5文件尾部多余空行，仅删EOF额外换行后冻结；已更新context-file-hashes.json。首次刷新hash脚本误以candidate为cwd导致docs未找到，未改变源码；之后用明确主目录路径刷新。

实际交付：默认semantic保留，summary/boundary/persistent按冻结策略显式兼容；History稳定ID/Unicode游标与有界公开读取、SQL统计、pending/action/result恢复、Core进展增量缓存；默认TUI/CLI/peer/移动只读视图有界恢复，TaskRecord/TaskResult另按当前身份取；conversation用量及cost采用SQL聚合。最终Provider准备认证后的实际JSON bytes，统一计数、重试与发送；同一S7任务账本覆盖main与辅助，部分/未知费用继续保守结算。新Task保存严格context_selection，漂移进入原等待决策，旧合同不伪造历史事实。

显式旧长日志迁移复用既有SemanticCompactor/HandoffWriter/window journals；容量或取消时已发布来源保留，重开继续。不自动改变策略，不截尾冒称来源覆盖。原字节、工具组、来源digest和最新真实user保留。冷public compact绑定Host线程后才使用Guard，结束/异常还原。普通短条数大内容的cap、remaining及完整前缀选择也接同公开effective_input_cap；源选择后按实际source长度推进，未覆盖尾部保留。预检不收费、不main发送，真实最终带tools请求再次检查；compact没有tools参数，不能把裸预检成功当之后任意工具请求授权。

自测索引：root-self-review.md、request-implementation-report.md、history-implementation.md、context-consumer-report.md。保留初次失败及修复记录：错name但valid digest、NULL subject最新结果、巨大cache读前字节检查、SQL畸形usage.tool_call、无覆盖旧历史压缩没有出口、Host小于API的普通大内容、固定指引自身不fit、EOF检查。consumer Thread46/46、Window34/34最终通过；Root23历史/52集成/10入口/4最终cap通过。它们不能替代本候选all-tests.log的最后outer CHAOS_TEST_SUMMARY。

性能：history-before/after与context-consumer-benchmark.json分开；首次流式来源校验及legacy display建立仍有成本，部分冷路径变慢。context 6000有效覆盖fixture热构建解码7行/8739bytes；semantic峰值55066bytes/31SELECT，persistent55501/36SELECT。前提、tracemalloc而非RSS、不同fixture及窗口metadata线性页扫明确见报告，不做跨fixture降幅。

回退：未升级live或写默认库；可从S9 accepted tree及S10 implementation.patch恢复隔离候选，主工作区仅按85白名单做逆向审查，不能整体reset或清理用户文件。Schema25的companion/index/cache不替代原日志；已迁移的实验数据库保留原事实，旧程序兼容不能靠盲删表证明。

独立监督：/root/s10_supervision已运行修复反例，已对冻结candidate复跑20项并通过、核差异与标准发现；最终决定待其报告。只有候选物理hash、原auth差异hash、标准30完整汇总及独立PASS同时成立才放行。


首轮标准30套件实际3284run/discovered、5errors/0failures/30skip、无未运行，未放行。all-tests-initial-failed.log、snapshot-initial.json、implementation-initial.patch保留83路径旧e0bebf34。两个实际进展回归根因是lease提高候选上限后epoch-only缓存错误复用旧identity；改为objective/candidate/hard的既有fingerprint匹配，参数变化有界重放但CAS仍比较旧durable cursor。小样本正向上调、反向缩小、目标切换及并发反例2PASS；全部HostTrace8PASS。计量fixture只该模块显式gpt-4.1并断言tokenizer存在，不放宽生产cap、不伪造fake Usage为计数证明；未知模型保守UTF8拒绝仍保持。两处oversized summary/boundary测试assert范围纳入更早build拒绝，零HTTP/零usage原断言不变。14项定向PASS71.294s后新freeze85、重新标准30。独立20项的旧PASS不能覆盖新快照，正在重跑并增lease反例。

新冻结独立21项全部PASS（38.231s，supervision-candidate-refrozen.log）；candidate_limit1→5→2、hard30→12、objective src→unrelated均与fresh observer一致，热路径禁止history页读仍通过，原15消息不变。仍等待新标准30套件与终审。

最终标准入口30套件：discovered=testsRun=3285，包含skip30；passed3255，failures0/errors0/expected_failures0/unexpected_successes0，未运行套件0，exit0。all-tests.log最后outer summary及final-test-summary.json是本候选的完整结果，Root660/660通过。end-validation.json确认85主/候选raw SHA和patch、原auth diff全部一致。独立终审最后决定待supervision.md，不以本段代替放行。

独立最终决定：PASS，supervision.md 与 supervision-snapshot-verified.json已物理核验，85/patch5a4fc346/原auth/标准30/21独立一致。可进入S11；未提交推送或升级live。
