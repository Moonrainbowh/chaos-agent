# S14 Workspace Feature 交付

单位验证通过，可进入 Host 集成；这不是 S14 阶段最终验收。

## API 与证明

`BatchApplyResult` 保留旧字段/默认构造，增加 `plan_id: str | None = None` 和 `post_identities: tuple[tuple[str, PathIdentity | None], ...] = ()`。实际 APPLIED 返回计划全部端点的不可变 tuple，缺失端点明确 None；case-only move 的两个名字均指同一实际对象。旧默认构造没有证明，Host 应核验 plan、端点集合和字段有效性后才持久 device/inode。

写入输出身份来自写入、flush、属性调整后的同一个临时 FD/handle，move 来自已持有源 handle 的 native rename 后完整状态；没有从新目标路径取得身份并认领它。batch 成功后验、错误归因和回滚采用完整 same_path_state（包括 size、mtime、mode/attributes），durable 证明仍沿用 v22 device/inode。发布后异常携带 publication_committed 和可取得的 output_identity；只有副作用标记而没有身份时保持冲突。兼容 post_identities API 仅观察当前路径，不能授权归属，docstring 和 Feature 契约已同步。

本次收尾也把 trusted_observations 纳入 inspection 异常处理；旧 mock/调用方真实写入却返回 None 时得到 PARTIAL_CONFLICT 并保留输出，而非泄漏异常或按内容回滚。

## 精确改动

以 base-tree.txt 的 1612a5e22e4b6a00f962c3e6b4f727b82daa6b7d 为基线。12 条实际文件与原始 SHA-256 在 workspace-feature-files.json；生成器逐条物理核验存在、读取 bytes、确认相对基线不同。10 修改：AGENTS.md、_batch_apply.py、_batch_editor.py、_batch_models.py、_batch_mutation.py、_batch_recovery_prepare.py、_secure_replace.py、_windows_atomic_replace.py、_windows_exact_move.py、_windows_replace_native.py。2 新增：_batch_output_receipts.py、tests/test_batch_output_ownership.py。全部位于 src/code_agent/workspace；本任务未修改 Root、Sessions 或 authentication。

## 实际验证

- workspace-completion-full.log / .exit：实际进程 exit 0，486 tests，21 skips，0 failures/errors，103.908s。包含新增11 ownership tests 的发现快照。
- 最后额外补入 legacy _execute_operation 返回 None 的实际写入案例后，workspace-completion-focused-final.log / .exit：实际 exit 0，最终12 ownership tests，0 skips，1.607s。产品源码在全量启动后没有变化；最终候选标准全量需发现487项 Workspace。
- 聚焦覆盖 create/update 同字节外部替换、publish 后 OSError 的 foreign 保留与正常 owned 回滚、后续步骤失败正常 owned 回滚、缺证明实际写入、默认空证明、delete missing 回执、真实 Windows normal/case-only move 完整元数据与外部替换、native rename 后错误可信恢复。所有实际文件在明确 TemporaryDirectory 子目录；未构造默认数据库。

Windows 下本轮验证 POSIX 专属21项依原 skip 处理；不据此宣称 Linux/macOS 已实跑。最终冻结、Root 集成与独立监督仍由主 Agent 完成。
