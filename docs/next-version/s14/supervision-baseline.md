# S14 独立监督基线

当前裁决：**不放行 S14，也不批准删减 snapshot/CAS/文件身份/journal**。主 Agent 尚未提交保留/简化方案。本监督只写 docs 下物证，不修改产品。

## 已核边界与实跑

已通读根 AGENTS.md、AGENTS.python.md、Host/Sessions/Workspace Feature 契约及 S14 requirements/status。使用候选锁定 Python 3.13，explicit main PYTHONPATH，所有仓储/工作区测试均显式 TemporaryDirectory 绝对路径；未构造默认用户数据库、未 reset/clean 真实工作树、未触及 authentication、未调用 Provider。

`supervision-baseline-tests.log`：10 个模块、50 tests / 17.237s / 2 skipped / 0 failures/errors。涵盖 durable batch 成功、故障回滚、取消等待、完成事务取消线性化、重启恢复、Windows case-only、同字节替代文件、缺失 POST 证明、外部修改零写入/中途冲突、旧 snapshot missing-only fallback、不改两根、损坏不 fallback、v10/v18 -> 当前 v26 schema、POST 证明 paired columns、legacy terminal 不绕 batch 状态机。2 skip 为 Windows 不适用的 POSIX/大小写目录测试，非故障场景全部覆盖声明。

## 旧 P1 裁决

1. 根《工作区的问题清单.md》仍写缺失持久身份，实际 v22 已增加 target_post_device/inode；Sessions API 仅 APPLYING 写、旧值缺失保 NULL；Workspace `_batch_recovery._path_position` 对会产生写入的 POST 要求身份，对不写入的 PRE 只比内容。现有崩溃后同内容替代/缺证明的保护实跑通过，旧措辞已过时。
2. 但**不能关闭身份 P1 全部窗口**：新反例发现 apply 成功与 `_persist_post_identities` 之间，外部同字节替换会被当成本次输出身份，继而取消回滚覆盖用户对象。见下节。runtime audit 把此窗口概括为保守缺证明仅覆盖崩溃早于落库，并未覆盖外部替换早于捕获。
3. 取消/部分冲突旧 P1 代码已补 `apply_edit_plan` 返回 recovered、Engine durable result 后才检查 token、TaskVerificationService 消费 workspace_may_have_changed。现有 dispatcher 取消 conflict 测试用 fake capture，故不能仅凭此条称真实文件 -> 通过 evidence -> cancel/conflict -> evidence 失效完整端到端已覆盖。最终验收需真实 batch/cancel/conflict 组合与 generation/evidence 物证。

## 新复现：输出身份捕获窗口

`supervision_ownership_window.py` 重用现有显式 Temp fixture，仅 patch Workspace apply 调用外边界：

1. 正常 Workspace apply update `before -> after`，真实批次输出成功。
2. 在方法返回给 Host 前，由 fixture 写一个独立新文件并 os.replace 到同路径，bytes 仍为 `after`（身份改变）。
3. CancellationToken.cancel，然后返回原真实 apply 结果。
4. Host 事后 observe 当前对象身份落库；恢复把该外部身份当作 POST，结果 `ROLLED_BACK`，内容变回 `before`。

`supervision-ownership-window-red.log`：实际 1 test / 0.453s / **1 FAIL**，输出 `OWNERSHIP_WINDOW rolled_back b'before'`；期望 PARTIAL_CONFLICT 且保留 `after`。

这是确定可重复用户对象归属错误，不是脚本模型/默认数据库/人为拼造 journal。根因定位 `src/code_agent/workspace/_batch_recovery_prepare.py:post_identities` 只观察当前路径；`chaos_agent/rewind_edit_batch.py:_persist_post_identities` 在 worker 已返回后才调用，未传递 apply 已证明的实际输出对象身份。

## 方案审查要求（尚未批准实现）

- 先提交最小方案。保留 apply CAS、owned-only rollback、durable journal、缺证明 fail closed、旧 NULL 保守行为及原取消结构结果。
- 可信方向：在 Workspace 实际副作用/最终复验边界取得并保留输出身份，Host 只持久化该可信回执；不得把之后观察到的任意新对象“认领”为输出。identity 的来源与失败行为须说明，create/update/move/case-only/delete 均覆盖。
- 不必为此增加 schema/table；现 v22 columns 可消费可信证明。数据迁移若无必要，禁止顺带迁移/GC/删旧记录。
- 必须直接复跑本监督反例，并覆盖证明持久化前崩溃、外部替换后崩溃、重复取消与 conflict、无冲突正常回滚、旧 snapshot/记录读取。
- dirty/untracked/non-Git 与 lineage/启动成本由主 Agent及职责成本审计另补；本基线 50 项不代替这些最终证据。

结论：旧保护值得保留，现有 50 邻近通过不足以放行；新 P1 先修复/限制受影响动作，才可讨论安全减负。等待主 Agent 的可审查方案。
