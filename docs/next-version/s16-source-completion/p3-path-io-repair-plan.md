# P3 path I/O 局部修复准备

状态：仅方案，等待Root在98f9a74整仓测试结束后放行；没有修改生产或测试。

已读取final-standards-review.md，确认调用链与实际实现一致。canonical_sources内实际WorkspacePathGuard构造及relative会执行文件系统I/O，不应直接位于async run/snapshot。

## 最小改动

1. `chaos_agent/child_runner.py` import asyncio；将启动`canonical_sources(request.required_sources, authorization)`改为`await asyncio.to_thread(canonical_sources, request.required_sources, authorization)`，其结果仍交原replace、create_thread及bind_child_budget，顺序不变。
2. `chaos_agent/source_completion.py` import asyncio；snapshot supplied比较先`await asyncio.to_thread(canonical_sources, supplied, self.authorization)`再比持久required。required为空且supplied为None的普通任务保持原直接返回。
3. snapshot配对完整read时将`self._full_read(call, message)`改为`await asyncio.to_thread(self._full_read, call, message)`。保留_full_read内实际restricted dispatcher身份解析、plugin覆盖检查、真实回执配对及Guard核验，不改Core/Sessions/预算/纠正事务，不新建执行器或缓存路径结论。

将整个_full_read移入worker而非复制其Guard逻辑，避免拆出第二条信任路径。resolve_action是同步原路由；asyncio.to_thread复制ContextVars，因此原冻结执行上下文在worker仍可见。此处不调用工具、不写共享ledger，线程返回单个path/None；snapshot的局部completed/open_calls及_cache只由事件循环修改。

## 定点测试

在现有tests/test_source_completion_contract.py增加必要职责用例，采用真实临时workspace、production runner/host和真实Guard，patch source_completion.WorkspacePathGuard为包装构造器，记录threading.get_ident与测试设置的ContextVar，随后调用原Guard（不以假Guard替代真实FS行为）。

- 启动、snapshot(supplied)及历史_full_read三路径分别观测新Guard构造；均必须不是event-loop thread且ContextVar值一致。生产完整读取仍能正常完成。这项测试修前将因loop thread匹配失败。
- 对已有成功read的fresh host snapshot，在worker Guard包装器中用threading.Event暂停（有界等待）；事件循环取消snapshot task，release worker并等待其结束；取消应传播，不提交局部cursor/completed缓存。再次snapshot必须从原durable history重新配对成功回执，completed保留；不吞CancelledError，也不为取消添加新持久状态。使用asyncio.Event/loop.call_soon_threadsafe通知worker已进入，避免固定sleep与竞态。

当前snapshot只在完整页扫描及末尾epoch/revision复核后写_cache；open_calls/pop/seen/completed均为局部copy。取消等待to_thread会留下只读worker继续，但其返回不产生事实/缓存副作用；下次回放不会因局部pop丢回执。已有纠正取消、epoch回放、完整配对反例保持。

放行后先跑上述红测试，应用三调用点修复，跑新增定点用例及现有source contract/public fixture与child factory邻近回归，不自行重复整仓全套。记录真实结果与阶段报告增补后停止等Root审查。
