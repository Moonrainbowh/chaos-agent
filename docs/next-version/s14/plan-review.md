# S14 最小方案独立审查

裁决：**PASS_FOR_IMPLEMENTATION（仅下述范围），不是阶段验收 PASS。**

已只读审查 plan.md，并复核 Workspace `_secure_replace.py`、`_batch_mutation.py`、`_batch_apply.py`、`_batch_rollback.py` 与模型接口。方案直指已复现的身份认领窗口，没有以更晚 observe 替换更早 observe；保留既有保护且复用 v22，不扩张职责，允许进入实现。

## 允许范围和顺序

1. Workspace Feature：底层 secure atomic publish、exact move、batch mutation/apply/result、必要 owned-only rollback 校验及相关接口导出/契约和邻近测试。只允许修复输出身份来源及传播，避免另建 journal/通用恢复器。
2. Workspace 验证通过后，Host Root 集成：rewind_edit_batch 消费已验证结果回执并持久现有身份列、必要集成测试。Root不重写Workspace安全行为。
3. Sessions无需改变schema/table/state-machine；若确需更改持久语义或新增字段，先回报重新审方案。
4. 原 post_identities 观察兼容 API 可保留，但必须准确标示不能授权认领当前对象。生产持久路径不能回退到它。
5. docs中职责、成本、保留结论、旧问题裁决和最终物证同步；没有证明安全收益则不删结构、数据或未知文件。

## 实现不可遗漏

- **可信对象来自副作用前持有的FD/handle**。create/update输出用临时对象FD identity；写入/flush后可从同一FD刷新完整状态，不能从目标路径刷新并信任新对象。move用已锁定source身份及同对象迁移；delete为明确missing端点。
- 输出回执的**完整身份**用于进程内成功postcondition、预期状态和rollback；device/inode对用于现v22 durable证明。不要因数据库只存两项而降低进程内same_path_state校验。
- secure write若publish后复验/cleanup异常，`publication_committed=True`只说明有副作用，**不证明当前对象归属**。有可信输出身份才可作POST，缺失则冲突；异常路径 `_classify_current` 不得按内容相同认领，不能只修成功return。
- BatchApplyResult默认无证明保持老调用者可构造；Host必须核plan_id/期望路径/端点与actual apply结果关联，重复/越界/错误身份回执拒绝或fail closed，不能认可模型输入的字典。
- 取消到达时仍先持久可信输出证明/真实恢复结果，然后上层停止；证明缺失、外部替代或旧NULL必须保留用户对象而非猜回滚。

## 最终验收门

- 独立 `supervision_ownership_window.py` 原反例变绿；原red物证保留不覆写。
- create/update/normal move/case-only/delete覆盖：Host落库前同字节外部替换；底层publish -> batch observe之间外部替换；publish后异常回滚；缺证明崩溃；真实输出正常取消回滚。
- 真实编辑与通过evidence后cancel+foreign conflict闭环：结果持久、code generation/subject变化、旧evidence不能完成任务，取消/暂停终态真实；无冲突完整回滚不伪变化。
- 原邻近50项、旧快照missing-only读取不改旧根、v10/v18/v22旧身份记录读取保持；脏/未跟踪/非Git/隔离与成本实测另补；最后冻结候选、标准完整测试和独立最终监督。

本审查只准实施上述最小P1修复；不准现在宣称S14完成，不批准简化/删表/GC/真实数据迁移，不涉及提交、推送或发布。
