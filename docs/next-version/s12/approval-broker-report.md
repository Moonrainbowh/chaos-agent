# S12 Interfaces 审批 Broker Unit

实现和独立于模型的 Unit 验证完成；不表示 S12 远程/真机验收完成。只修改 Interfaces，未修改 Root、Remote、Sessions，未启动真实 Application 或 live Host。

公开用法：`ApprovalBroker(repository=None, ttl_seconds=300, clock=time.time)`；原本地 `request(request, cancellation)` / `next_request()` / 同步 `resolve(action_id, approved)` 保持兼容。持久请求加 `execution_context=ActionExecutionContext(...)`、`workspace_root=Host真实执行根字符串`。没有 typed Task 时仅本地，不能出远程授权卡。

`await broker.pending(task_id)` 返回仓储最多100条持久卡，不消费本地队列。`await broker.respond(host_request_uuid, task_id=..., action_digest=..., state_version=..., owner_instance_id=..., approved=True|False, authenticate=callback)` 重新认证、核精确绑定、持久 CAS，再仅首次唤醒当前 live waiter。callback 可同步 bool 或返回 awaitable，须严格 True。无 waiter 的旧卡拒绝，重复消费不再次唤醒；外部预消费不能复活当前 waiter。本地键盘 bool 也在 request 返回准许执行前消费 CAS，失败不返回准许。

完整动作 name/原args/可信target/edit-plan/context/仓储binding canonicalJSON 哈希绑定；远程 preview 只展示有界 safe action_activity 和 edit-plan ID/digest/risk/path数量，不输出原参数正文或 diff。明确有限预览及 diff 可在本地查看。ApprovalPersistence 不执行任何动作。持久等待有 TTL，超时返回 False；取消原异常传播，清 waiter，卡保留 expired/stale 事实。

验证：`interfaces-broker-suite-final.log` 的最终完整 Interfaces 套件 **684 tests / 37.838s / OK**，包括新增11个标准discovered测试，其中3个真实显式TemporaryDirectory SQLite测试，构造前断言数据库为Temp绝对路径。覆盖本地持久拒绝、真实远程批准、取消、TaskState漂移、期限、跨绑定/设备撤销、重复点击、外部预消费、重启无waiter、无Task仅本地。首轮680个测试日志保留；真实Repo focused 10个的早期日志保留。

跨进程撤销与 SQLite 消费不是单靠 callback 天然原子：上层需持有设备Store消费/撤销门，或明确真实保证边界；Broker异步锁只串行本Broker回答。Root还需在原Dispatcher审批点传typed context/workspace、给共享Broker注入仓储，并为 primary/备用Remote应用启用正确交互。S5未知动作核对、等待决定与设备服务仍由原Host/Remote实现。

本次文件及原始SHA256见 `approval-broker-files.json`。产品编辑已停止，可进入Root组合/独立审查；不提交/推送、不碰默认用户库。
