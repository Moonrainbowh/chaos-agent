# S10 独立监督结论

结论：**PASS**。仅针对下列物理冻结 candidate 与 S10 范围；监督者未实现产品源码，未提交、推送、操作真实 Host/Provider 或访问默认用户历史库。所有独立仓储测试在显式 TemporaryDirectory 中运行，构造、重开和直接 SQLite 前核 resolved path 在该目录。Python 为候选 `.venv/Scripts/python.exe`，CPython 3.13.2。

## 冻结与最终物证

- S9 base tree：`1161bad09f69f1517df6e55763f6141fdeb5178b`。
- 85 个 snapshot 路径在 main 和 candidate 的原始字节 SHA256 均一致；删除路径核不存在。patch SHA256：`5a4fc346668816db3939b5c8e61ff9dfcf7c1f906fbe855ccfd814f36ace059a`。
- 原用户 authentication 三路径 diff SHA256 保持 `7cbeb87002763fbe5de0bf33a4fd68193af6d66e5340be75f2a1edeb776befca`，未纳入本阶段 snapshot。
- 独立 `supervision-snapshot-check.py` 最后读取 all-tests.log 的外层 `CHAOS_TEST_SUMMARY`，30 suites / 3285 discovered=run / 0 errors / 0 failures / 30 skips / 0 unexpected successes / 0 unrun，exit 0；与 final-test-summary.json 和 end-validation.json 相符。物证：supervision-snapshot-verified.json。
- 以 candidate 的 cwd、src 和根 PYTHONPATH 统一执行 `supervision-counterexamples.py`：**21 tests PASS，38.231s**。物证：supervision-candidate-refrozen.log。首次83路径与初始全量5 ERROR物证另存，未覆盖为成功。

## 独立反例覆盖

21项包含真实6000来源冷读、suffix增量和重开；旧摘要source锚点、稳定UUID和Unicode fragment；错误tool name但同ID仍不闭合；同序号正文修改的cache并发失效与event recovery version漂移；17MiB损坏cache拒绝先于JSON decode；未知usage预留重开不双计数；旧三种window策略重开及策略漂移拒绝；semantic显式分批迁移取消后保已发布prefix、重开续进；summary/boundary/persistent显式旧长日志恢复和真实最终preflight；最新RUNNING与NULL subject TaskResult不被晚telemetry挤掉；SQL usage与既有Accumulator语义对照；有界UI restore保accepted_partial完整result；Mobile项目归属在body读取前校验；/cost禁止全事件读取仍使用SQL聚合；真实Factory冷compact绑定及异常reset；保存context_selection同策略仅output改变也拒绝漂移、旧无facts不补造；低于1000行的大原文经显式compact后符合真实Host ceiling及persistent remaining；Unicode arguments/schema实际serialized扩张超限零reserve/HTTP。

最后新增lease反例在原15条持久消息不变时，candidate_limit 1→5→2、hard limit 30→12及目标src→unrelated都与新鲜完整来源观察结果一致，随后禁止read_history_page仍能hot返回；禁止全量load_messages。它核验正式全量发现的cache身份变化修复。

原始完整组和必要user须保留；持久策略在原user文本后附加公开history_ref，已核原文前缀，UUID/Unicode offsets另核原始History读取。小行数反例Host5000、API200k/work150k，实际preflight后cap/remaining≤4936；persistent零HTTP，summary仅辅助摘要HTTP。较小Host1000无法容纳persistent固定guidance的负向仍拒绝，未增加生产额度。

## 发现与处置

监督实际发现并保留初始失败：工具组只认ID、超大cache无decode前限制、无window旧长日志缺显式恢复入口、NULL subject过滤当前result、invalid tool_call的SQL usage语义不符。首轮标准全量又发现lease candidate_limit变化时cache重建CAS回归。修复后独立与标准验证全部通过。

首轮标准5 ERROR中的两处oversized prefix属于提前build拒绝而测试断言只包stream；已扩大断言验证范围，仍核零HTTP/零usage。S9 Host trace fake模型原名test触发unknown UTF8保守上界，进展语义fixture改明确gpt-4.1并核tokenizer存在；未加Host/Task额度。此改动合理：进展续租与tokenizer缺失是不同变量，unknown实际请求超限拒绝仍有覆盖。监督自身fixture误猜EventKind、漏ContextConfig字段、unbound binding语义、persistent引用标记和旧错误文案等均单独说明并保留日志；未作为产品缺陷混记。

## 通过范围与明确界限

默认继续semantic；其余三策略为显式兼容，Task冻结事实漂移须拒绝。生产Host总prompt20k、rules6k不变，实际prepared请求统一复用Guard和S7 reservation/settlement。未知模型UTF8字节上界/tokenizer计数属于本地估计，不等于Provider usage；未知usage负债不伪装为零。未校准图像在HTTP前拒绝，Codex无native output cap保留诊断。本次没有真实模型质量、真实Provider或新跨平台CI验收结论。

无checkpoint/window的超长旧日志必须显式compact，不能冷build静默截尾或替换旧策略。原始active读取1000行/4MiB、128派生记录及单摘要文本限额是资源边界；完整tool group或必要prefix/user/carry最低量仍不fit时明确失败，取消保已发布prefix以便验证后续进。这些边界没有被成功回执掩盖，符合S10恢复门和最终请求不越额度要求。

本监督未开展S11；本次S10门已通过。
