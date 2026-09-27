# S5-1：验证 Evidence 基线与复用边界

## 现有事实

项目已经有一套 append-only verification ledger，不需要另建第二套缓存：

- `EvidenceRecord` 固化 `criterion_id`、`generation`、`subject_hash`、`output_hash`、outcome 和 provenance；
- `LedgerTaskVerificationService.assess()` 只把同时匹配当前 `code_generation` 和 `subject_hash` 的 evidence 转成 completion candidates；
- 写入、已实际尝试的命令以及可能部分写入的 edit plan 会导致新的 subject/generation，旧 evidence 不再满足当前完成条件；
- 自动 required PASS 只能来自 `EvidenceProvenance.SYSTEM_VERIFIER` 或确定性的 `SYSTEM_PLANNER`；对明确的手工 criteria，`USER_CONFIRMATION + manual_only` 是单独允许的人工验收例外，模型文本和任意 shell 输出不能直接完成任务；
- 当前 planner 在已有当前代风险验证通过时可以不再建议下一步 verifier，但模型直接重复调用 `run_verification` 仍会经过真实执行路径。

## S5 fast path 的必要条件

只有以下条件全部成立，才允许考虑复用已有 verifier evidence：

1. task、workspace root 和 contract revision 相同；
2. 当前 workspace `generation` 和 `subject_hash` 与 evidence 完全一致；
3. verifier kind、targets、受 Host 约束的 cwd、验证参数和目标 criterion 完全一致；
4. evidence outcome 为可信 PASS，provenance 为系统 verifier 或确定性的系统 planner；
5. evidence 对应的 verification run 已正常关闭；
6. 其后没有 write、实际尝试的 `run_command`/`run_process_v1`、可能部分写入的 edit plan、rewind/recovery 或 workspace identity 变化；
7. fast path 只减少重复 verifier action，不改变最终 completion gate，也不把旧 evidence 投影到新 generation。

## S5-1 结论

当前证据足以确认“旧 evidence 在 generation/subject 变化后不能复用”，但不足以直接修改生产执行路径。下一步应做 S5-2 对照实验：在同一 generation 下重复相同 verifier、在写入后重复 verifier、改变 targets/criterion 后重复 verifier，并验证 candidate 只在第一种情况下命中。

S5-1 不修改验证调度策略，也不声称已经减少了验证步骤。
