# S4 规则压缩与预算加载

状态：DONE。用户已明确授权默认额度调整为6,000，正式规则压缩与默认额度已应用。新独立监督PASS，允许进入S5。

## 已完成且可审查

- before.json / probe.py：真实工作区加载链。根AGENTS 1896、Python1143、Core5468、Interfaces14163、Host8727本地估算token；代表位置全部超过3,000。统计的是继承链，非全仓规则总量。
- root-proposed.md、python-proposed.md、core-proposed.md、interfaces-proposed.md、host-proposed.md：用户授权后应用正式路径，reference保留原始快照。constraints-audit.md逐类列出接口/宿主强制约束、数值及冲突处理；没有将Units中的安全条件整体迁出。
- reference/*-before.md现为S3 base tree逐字节原文，reference-manifest.json记录SHA；*-precompression.md另外保留物理压缩前文本。四份差异仅工作区CRLF与Git LF；Interfaces压缩前还含已新增的S4诊断契约，不能把该版本当S3原文。独立监督发现并核验了这项证据纠正。
- proposal-contexts.json / probe_proposals.py：隔离副本中真实RuleLoader和WorkspaceContextBuilder，使用生产windows_system_prompt及compact_definitions(tool_definitions(include_git=True))，无Provider调用、无模型成绩。3,000额度根通过、其余阻断；6,000额度四位置通过。完整渲染Core3425、Host4880、Interfaces5778 token。总提示上限20,000不增加，工具/system/消息预留仍检查。实验不含mode/Skills/peer后缀，也不是完整产品profile最终预算验收；后者在确认后继续。
- RuleLoader输出完整渲染用量、相对加载路径/继承深度、显式预算修复方法，不截断。总字节超限列出失败位置与已加载路径。WorkspaceContextBuilder组合规则/system/tools/state后失败提供数字诊断，显式规则扩容不能绕总提示与最小消息预留。
- BudgetDiagnostic仅允许相对路径与已知非负预算计数；终端只展示这类本地安全事实，不回显规则正文、绝对路径或任意错误正文。旧generic安全提示保持兼容。
- 实际复现旧ThreadAwareContextBuilder在规则检查前调用摘要Provider。WorkspaceContextBuilder新增无Provider固定内容preflight，自动构建和手动compact先检查再摘要/发布checkpoint，最终build仍复查；新策略context_windows本来先构建scaffold，不修改其算法。该修复不改变S10历史策略选择。

## 验证

- Context全套188 PASS：context-final-tests.log。
- Thread Intelligence全套36 PASS：thread-final-tests.log；自动/手动超限反例均零摘要调用、零checkpoint。
- Interfaces相关诊断7 PASS：ui-diagnostic-tests.log，原隐私反例保留。
- 默认create_application真实装配1 PASS：default-budget-test.log。超限在真实Engine ContextBuildError链中阻断，模型调用0、dispatch0、MODEL_STARTED/ACTION_STARTED0，物理文件原样；终端显示数值与AGENTS路径，不显示正文。
- semantic-preflight-before.log保留旧摘要调用=1反例；diagnostic-before.log保留旧诊断缺文件的反例。新增集成测试曾有测试自身字段/异常协议错误，已按实际engine._actions与ContextBuildError修正，未放宽零调用/文件断言。
- git diff --check通过。S3快照与用户authentication原diff再次核验仍一致。
- 默认6,000应用后Context全套188项PASS，见context-default-final-tests.log。旧断言默认3k和旧超限样本已改为新6k；组合容量测试仍覆盖超过规则额度但能容纳system的正例，超限负例仍真实阻断。
- 首次根全套587项只1个既有budget fixture失败，root-default-fixture-failure.log保留。该fixture的rules1900→3900，使规则仍≤6k、system+rules>6k且≤8k；全部原断言保留，所在文件5项PASS。随后最新根全套587发现/运行、0skip/failure/error、exit0，223.681s，见root-final-tests.log；不是用旧失败运行冒称通过。
- 20文件S4独立diff基于已审S3 tree，patch SHA256 `036f854eafbba4a1c41e6bd32e912606dbd13a4d082d51f33931d2285ac06107`；snapshot/end-validation物理核验主/候选一致及用户authentication原diff未变。

## 应用与待独立监督

用户已确认3,000→6,000，总提示仍20,000。正式四位置RuleLoader/ContextBuilder通过；追加参考路径后的规则实际量见after.json。default-host-contexts.json从create_application完整默认wrapper进入模型回合，真实模式/系统提示与load_tool_contract/read/search/write/edit/execute披露保留，断言每份规则全文存在，4次离线模型回合成功。原始参考不是默认注入源。该实验选择问候避免无关仓库扫描，不是Provider或长历史容量验收。

独立监督见supervision.md：逐类核对强制约束，候选亲跑Context188、Thread36、相关集成13通过；9个独立超限反例均零摘要/无checkpoint/用户文件原样，四个完整Host回合通过；亲核根587结果与参考基线SHA。没有阻断问题，正式20文件hash吻合。

本阶段没有提交、推送、合并或发布。根全套及独立监督已完成，S5—S16在本阶段结束时尚未实施。
