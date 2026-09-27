# S5-2：Verifier 复用对照实验

## 对照矩阵

| 场景 | generation/subject | verifier 参数 | 当前 ledger 结论 | 是否可复用 |
|---|---|---|---|---|
| A：同一 verifier 重复调用 | 相同 | kind、targets、cwd、criterion 全相同 | 当前证据仍有效，但第二次模型调用仍会执行 | 候选可复用 |
| B：目标不同 | 相同 | targets 不同 | `EvidenceRecord` 不保存 verifier identity，当前 completion candidate 外观相同 | 不安全，必须拒绝复用 |
| C：写入后再验证 | 不同 | 参数相同 | `assess()` 过滤旧 generation/subject | 安全拒绝 |
| D：验证失败后重试 | 相同 | 参数相同 | FAIL 不能构造 required PASS | 不复用 |
| E：验证运行未闭合 | 相同 | 参数相同 | completion/assessment 现在只读取 completed run 的 evidence | 不复用 |

## 实测证据

S4 真实 Trace 对同一小型 Python fixture 连续执行两次相同 unittest：两次均成功，三次重复运行中两次验证合计耗时为 234–439 ms。这证明重复 verifier action 存在可测成本。

已有测试和本阶段修正覆盖了 C、D、E 的安全边界：写入后旧 evidence 过期；失败或不可用结果不能构造 required PASS；未闭合 verification run 的 evidence 不再进入 completion assessment，不能参与最终完成。

## 关键阻断

当前 `EvidenceRecord` 只有 criterion、generation、subject、output hash 和 provenance，没有保存 verifier kind、targets、cwd 或受约束参数的 identity。因而在相同 generation/subject 下，`python_unittest(targets=["a"])` 与 `python_unittest(targets=["b"])` 不能被 ledger 区分。

因此 S5-2 不接入新的 verifier cache。当前 planner 已有一个按 criterion/generation/subject 的重复建议抑制路径，但它不是安全的 verifier-result cache，仍然缺少 target identity。直接按现有 evidence 判断“同 generation 就复用”会产生错误完成风险。S5-3 必须先增加一个有界、持久化的 verifier identity，并让旧 evidence 在 identity 缺失时保守地不可复用；最终 completion gate 仍重新复核 generation、subject、criterion 和 completed run。
