# v3 首次真实复验：结构与字面值检查通过，语义未通过

记录日期：2026-10-09（客户端 Asia/Shanghai）。实际日志保留运行主机原时间戳。

本次 [90c7abea4a20](../fast-acceptance/attempts/s16-source-completion-p4-90c7abea4a20/worker-result.json) 使用原 GLM-5.3-flash / medium / 4096，原四来源和原预算。6次真实响应、132.633秒、32655 tokens（27128输入+5527输出，child7971已经包含），6条唯一用量全settled。无补全、无额度续期；新v3的独立与比较阶段均结构接受，最终 `completed / unchanged / unverified`，仍保留 risk-appropriate-validation。

[运行物证审计](../fast-acceptance/attempts/s16-source-completion-p4-90c7abea4a20/runtime-audit.json)核对wire哈希、原模型参数、完整来源、阶段隔离、完整子报告、可见正文与持久rendered相等，以及候选/fixture前后未变。它不判语义。

**独立语义结果：PARTIAL_WITH_ERRORS / NOT_ACCEPTED。** [独审](../fast-acceptance/attempts/s16-source-completion-p4-90c7abea4a20/independent-review.md)和[核对表](../fast-acceptance/attempts/s16-source-completion-p4-90c7abea4a20/checks-review.json)均来自 GPT-6.1-sol / medium 的只读复核，父agent也已直接核对实际原稿与来源。

改进：五个当前静态输出正确；纠正子报告duplicates“当前恰好通过”为失败；排序作为特定漏检见证成立且限定范围；引用和中文交付恢复。

仍失败：

1. empty的missed描述“清空所有输出”，反例却报告原字符串，不能来自同一错误行为；字面值各自满足不等式，仍不保证行为→输出相容。
2. 最终仍把child的empty“对1-5条均不具区分力”判正确，理由只是当前实现通过，量词推理仍错。
3. comparison覆盖了子报告段落，却只引用其中前半句，漏掉“测试缺少重复保留直接覆盖”的错误主张；trim后去重产生单个A，会被双A期望拒绝。
4. no_mutation的具体fault/actual对应不充分，不能由审查者替模型补造修改操作。

因此v3修复了低层字面值一致性检查缺口，但没有完成父复核的语义修复。剩余设计缺口集中为“错误行为与静态输出的对应”及“段落覆盖不等于原子主张覆盖”。用户继续授权后，新实验仅评估这两个窄任务，保留本次失败，不回填原稿、不将探针结果当生产验收。
