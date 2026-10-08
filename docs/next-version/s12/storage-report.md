# S12 Sessions 中央审批/待决定账本

Unit 实现已完成；最终 Sessions 全套281run/2skip/零失败错误通过（59.604s）。只使用显式 TemporaryDirectory 数据库，没有操作用户默认库、真实 index、authentication、live Host 或提交部署。

## 公开 API

- `approval_binding(task_id, workspace_root, *, kind='approval') -> dict`：返回 task_id/thread_id/workspace_root/state_version/owner_instance_id/kind。审批仅运行/验证任务且持久 owner 存在；待决定仅暂停/中断/等待决定且 owner 已释放，不自行探测进程。根来自 lineage.worktree_root 或旧 contract.authorization.workspace_root，请求不能授予根。版本覆盖完整 Task、TaskState 与更新时间、完整 execution owner；待决定额外覆盖有界 SQL 历史 revision。
- `create_approval_request(*, request_id, action_id, action_digest, preview, binding, expires_at) -> dict`：Host UUID 与模型 call ID 分离，摘要为完整参数 SHA256（Host 生成），预览 JSON ≤8KiB，UTC epoch 秒 TTL 最多1小时。同 UUID 内容相同重试幂等，漂移拒绝。预览脱敏由中央 Host 构造负责，本库不解析任意模型授权。
- `list_approval_requests(task_id, *, limit=100) -> tuple[dict,...]`：最多100条，pending 对账为 expired/stale，原卡保留。
- `consume_approval_request(request_id, *, task_id, action_digest, state_version, owner_instance_id, approved, decision_transition=None) -> dict`：同事务核绑定/当前状态/owner/TTL并单次消费。同响应且当前绑定仍有效幂等，`consumed_now` 首次 True、重试 False；相反响应/跨任务/摘要/版本/旧 owner/过期拒绝。可选 transition 仅 approved decision + WAITING_DECISION，显式 accepted_partial/failed 与消费同事务；错误回滚全部。返回 task_status/task_updated_at/post_state_version，终态重试须复核相同 post version。后续真实任务/历史变化使旧回应失效，不再触发执行。
- `invalidate_approval_requests(*, request_id=None, owner_instance_id=None, current_owner_instance_id=None) -> int`：恰一个 selector；单 UUID 可失效任一种卡，owner 选择仅失效审批，quiescent decision 可在重启继续核对。SQL 更新，不物化无限记录。

账本只记录显式决定；普通 continue 仍由 Foreground 的 owner CAS 激活，未知结果仍由原 S5 对账闸门处理。本接口不执行动作、提供 shell API 或开放新权限。

## 验证证据

新增18项测试全部通过，包含精确重复/冲突、过期持久化、跨任务/摘要/版本/owner、generation 漂移、Task 状态漂移、重启/新 owner、单卡取消、预览/TTL/列表边界、并发消费、执行 worktree 根、旧 v25 非破坏迁移、无 owner 待决定、历史 revision 漂移、原子终态与错误回滚。测试构造每个仓储前显式断言临时绝对路径。

首次全套272run/2skip 有1failure+1error：既有迁移测试硬编码25，以及通过降 user_version 模拟 v23 时未删新 v26 表。仅修正临时 fixture 清除新表及最新 schema 期望；原消息/迁移业务断言保留。第二次277run/2skip全部通过，随后新增原子待决定终态要求，第三次281run/2skip/零失败错误通过。日志保留 `storage-suite-initial-failed.log`、`storage-suite-second.log`、`storage-suite.log`、`storage-unit-tests.log`。

生产 RuleLoader 实测 Sessions 5987/6000 token，总提示上限20000未变；契约新增约束并同义压缩 schema 描述。v26 migration、列、FK和索引结构校验已加入。精确源码/hash在 `storage-files.json`，只含10个 Sessions 路径。
