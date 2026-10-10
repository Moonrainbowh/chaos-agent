# 两个窄探针独立审查

结论：`PARTIAL_WITH_ERRORS`，两个探针均未通过其职责验收，不支持直接接入生产。它们只复核 90c7 冻结失败报告，不是新的 S16 整链验收；90c7 的 `NOT_ACCEPTED` 不变。

审核只读取脚本、原四来源、原 final/advisory、两份结果的最终 text 与 usage。未执行来源、测试或 Provider；未把流中推理文字当交付答案。

## 见证一致性探针

识别 no_mutation 原地 trim 应使输入成为 `['A','']`，与报告所列 `['A','A']` 不符；也指出 empty 的非空输出未从该空输入给出的行为中导出。这些是有效发现。

但 `test_empty/missed` 原 fault 明确为“实现清空所有输出”，探针改写成“对 [] 返回 []，但对其他输入不做 trim”，并判 consistent。这以另一种错误实现拟合已给出的 actual，漏掉同一“清空所有输出”实现对 `[' Alice ']` 必须返回 `[]` 的直接冲突。提示中的“不得补造操作”未成为有效约束。

另有职责偏离：no_mutation/detected 的 derived_actual 描述当前 `list(values)`，而非同记录所述候选行为；其他条目把泛称“违反其他条款”补成去重/排序等特定操作，没有保存应有 unknown。输出派生观察是说明字符串，不能由 Host 作为纯 JSON 字面值独立比较。第一项 reason 说“无法从空输入按任何候选行为导出”也过宽，固定返回一个元素的候选显然可产生非空值；应限于所给具体行为。

因此不能把该探针的 consistent 当证明，也不能只采用其中两个有效报错而忽略其误判。

## 逐主张覆盖探针

找到 advisory 首段的重复覆盖主张没有对应 comparison（index=null），说明它确实比段落 ID 覆盖更细。但该事实被标 unknown 而不是 omitted，reason 又把当前实现在 duplicates 失败与是否存在重复保留覆盖混合。直接的辨别例仍是 trim 后去重给 `['A']`，被期望 `['A','A']` 拒绝。

最关键的剩余错误是把“test_empty 对 1-5 条均不具区分力”继续判 covered_correct，理由仍只是当前 `clean_names([])==[]`。这不能推出零检测能力，固定返回非空元素的错误会被拒绝。

还把子报告“duplicates 恰好通过”标 misjudged，却在 reason 中正确承认 comparison 已指出子报告误判。任务要求核对 proposed comparison 的 assessment：此处 comparison 的纠正本身是正确的，应与“child 原主张错误”区分。状态方向不稳定，不能直接据此控制生产交付。

## 成本与边界

两次实际发送、零工具，模型 `glm-5.3-flash`、`medium`、每请求输出上限 4096 不变；manifest 记载合计 67.85018890000356 秒。

| 探针 | input tokens | output tokens | total tokens |
| --- | ---: | ---: | ---: |
| fault_observation_consistency | 2447 | 2092 | 4539 |
| atomic_claim_coverage | 3964 | 1626 | 5590 |
| 合计 | 6411 | 3718 | 10129 |

执行完成、没有工具调用和预算合规是执行事实；语义质量仍未通过。报告只引用现有物证，未自行重跑。

## 最小后续方向

暂不接入生产，也不通过更多同输入重抽制造 PASS。首个职责的可检验改动是物理隔离已声称的 actual/expected/verdict：给原 fault、两个输入、观察对象和源码，让模型只返回 exact fault 引文、明确行为或 unknown，以及纯 JSON 的派生观察；本地随后与冻结原稿比对。此处 Host 比较仍不证明行为推演正确，只能把输入隔离的效果与当前 fit 已给结果的失败区分。该后续探针须单独审查，不能升格为 S16 PASS。
