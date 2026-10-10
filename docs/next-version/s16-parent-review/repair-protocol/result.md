# 父复核协议与纠错修复

2026-10-08；工作树 `codex/s16-parent-review`，基线 `b28868a` 加未提交补丁。用户授权先修复，明确不跑全量。本轮没有真实 Provider 调用，没有执行 S16 fixture 中的函数或测试。

## 已修复

1. **可操作的错误反馈与定点修正。** 补全请求带当前阶段原稿、JSON pointer 和具体错误。Host 从原稿重新计算允许改动的位置，只应用这些字段/无效项的替换、删除或缺项追加，然后重新检查整份输出；正确字段保持原样。无法解析为对象的原稿才允许整体替换。独立阶段不会接收旧比较稿或 child advisory。总计仍最多一次原预算补全，旧 v1 快照继续按旧引用及完整回复协议恢复。
2. **来源元数据由宿主绑定。** 新 v2 输出只需来源路径与物理行范围，宿主绑定冻结版本并提取准确原文；显式填错的版本、引文仍被拒绝，不用自动覆盖掩盖错误。三项源码判断与报告比较必须提供源码引用。任务/子目标中的流程事实可引用明确的宿主记录，独立阶段只见来源准备和复核请求无工具的事实，child 生命周期仅比较阶段可见。运行事实不证明语义，结论保持 `unverified`。
3. **末次失败和容量边界可靠留存。** 完整无工具复核响应的每次结构校验结果，与阶段状态通过同一 CAS 事务保存到独立 `parent_review_attempt`；包括最后一次拒绝原文及具体错误。有效修补结果不同于原文时另存，原文不截断、不进入普通消息。原始/有效输出各最多100万字符，独立审计16MiB上限覆盖允许内容的最坏 JSON 转义，不增加模型额度。快照溢出时仅写小型 blocked 状态并引用旧证据，保留已消费补全额度；恢复不会继续准备或补全。

非有限 JSON 数字、过深结构及非法超长补丁路径被转为有界校验错误，避免诊断数据本身使审计失败。流中断、工具调用协议拒绝继续沿用原调度与事件记录，本轮不宣称它们全部纳入该结构审计。

## 验证

- [真实失败初稿离线回放](replay-result.json)：原 `task_objective.citations=[]` 被明确定位。v2 只补 `/findings/3/runtime_refs` 即通过结构检查；所有原判断均保持，原稿中已知错误的空输入测试概括仍存在，评分明确 `PASS_STRUCTURAL_REPAIR_ONLY / semantic_acceptance=NOT_ACCEPTED`。零 Provider、零 fixture 执行；脚本为 [replay.py](replay.py)。
- [相关回归](targeted-tests.log)：**77 项 / 344.155s / OK**。覆盖 Core 协议、Sessions 原子审计、实际父子请求装配、来源完成及暂停/预算等相邻路径，没有运行整仓。
- 运行该组期间补充了旧快照贴近131072字节的容量反例，以及数值/旧协议边界；对应最终文件另行定向确认：[Core 17 项 / 0.006s / OK](core-final-tests.log)，[Host 6 项 / 42.158s / OK](host-final-tests.log)。这两组与77项有重叠，不能相加当独立测试总数。
- 原 near-capacity 反例在真实 Host/Sessions 中先报 `ValueError: context record is too large`，修后确认旧证据、完整原文与已消费补全状态同时保留；红→绿记录见 [容量回归](../fast-acceptance/near-capacity-red-green.log)。
- [独立代码审查](independent-review.md)通过，范围为本轮结构修复；审查者未自行运行 Provider 或全量测试。
- `git diff --check` 通过。测试使用 Windows / Python 3.13.7，真实生产装配中的模型响应由脚本提供，不冒充真实模型验收。

统一定向命令：

```text
.venv/Scripts/python.exe -m unittest src.code_agent.core.tests.test_parent_review_contract src.code_agent.core.tests.test_parent_review_repair src.code_agent.sessions.tests.test_parent_review_attempt tests.test_parent_source_review tests.test_parent_review_evidence tests.test_parent_review_boundaries tests.test_s16_source_completion tests.test_source_completion_contract
```

最终边界复验分别使用上面的两个 Core 模块，以及 `tests.test_parent_review_evidence tests.test_parent_review_boundaries`。不因文档或最后的输出措辞调整重复长回归。

## 交付边界

本轮完成此前建议的前两项：纠错回路及宿主/模型证据分工。没有证明模型已经能够正确判断测试能力，也未把小字段修补带来的结构通过计为语义通过。**S16 真实质量验收仍为 NOT_ACCEPTED**；原两次失败、用量及 unknown 边界保持。下一阶段才是小范围语义验证及必要的真实整链复验，不能跳过这一层宣称整体完成。

未提交、推送或合并；未跑全量、跨平台 CI 或 UI 验收。
