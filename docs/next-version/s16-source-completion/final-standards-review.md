# Standards 独立审查

审查轴：Standards。固定比较 `git diff afbd8f3ee7163cb71959a6af0bdfb77b6c082cdd...HEAD`；HEAD 为 `98f9a744650890c69a305ae643443b384006123e`，区间仅一项 commit。已读根、Python、Host、Core、Context、Orchestration、Sessions 契约及实际生产 diff/new files；P4 待运行验收不作为源码标准违规。

## 硬违规

1. **[P2] 必要来源的路径核验同步阻塞事件循环。** `chaos_agent/source_completion.py:14–20` 新增 `WorkspacePathGuard(...)` / `guard.relative(path)`；它们从 `chaos_agent/child_runner.py:72` 的 async `run` 和 `source_completion.py:73,103` 的 async `snapshot` 直接执行。`WorkspacePathGuard` 构造实际执行 `exists/is_dir`、root resolve、目录身份读取，`relative` 还逐组件 `is_symlink/lstat` 并 resolve；不是纯词法操作。新完成门每次回放实际读取回执时重复这些文件系统操作，慢磁盘或网络工作区会延迟整个共同事件循环的取消、时间监督和并行子运行。确切规则：`AGENTS.python.md:6`，“异步边界显式async/await，阻塞文件I/O经asyncio.to_thread。”将启动核验和 snapshot 页内的回执路径核验放入 `asyncio.to_thread`，保留同一 Guard 及闭合检查。此项来自调用链静态核验，未声称复现实际卡顿。

## 启发式 smell baseline

已按 Mysterious Name、Duplicated Code、Feature Envy、Data Clumps、Primitive Obsession、Repeated Switches、Shotgun Surgery、Divergent Change、Speculative Generality、Message Chains、Middle Man、Refused Bequest 检查实际 hunks；这些均仅为判断建议，仓库契约优先。本次未发现额外具有当前实际影响、值得要求重构的 smell。

Core 保持 typed snapshot/注入协议；Host承担身份与路径适配；Sessions使用原 checkpoints/messages、同事务纠正和原预算账本，未新增表/第二状态库。未发现新的 Python 3.10 不兼容点。

Standards 合计：1 项硬违规，0 项额外 smell；本轴最严重 P2。未修改生产、未调用 Provider、未启动服务、未运行全量测试、未 commit/push。

## P2 修复后独立复核（2026-10-07）

结论：**PASS**。保留上文历史 finding；针对 HEAD `98f9a744650890c69a305ae643443b384006123e` 上当前未提交修复进行只读生产源码复核，原 P2 已消除，本次范围无新增 blocking。

- 逐调用点核对：`EngineChildRunner.run` 启动 `canonical_sources`、`ChildSourceCompletion.snapshot` supplied 规范化、历史配对 `_full_read` 均由 `await asyncio.to_thread(...)` 执行。`canonical_sources` 仍使用原真实 `WorkspacePathGuard`，`_full_read` 内 Guard 也包含在 worker 调用中；全仓检索未发现这两函数另有生产同步调用入口。未声称仓库所有 Guard 都被异步化。
- `authorization` 仍为原父冻结对象，完成门 Guard 未新增 allow_sensitive/allow_outside；restricted route、插件身份拒绝、真实 request/result 配对、完整回执与持久要求比较保留。线程只返回路径或 None，不调用工具或写预算/纠正状态。`to_thread` 复制 ContextVars，新增真实 Guard 包装测试验证启动、fresh history、supplied 三路径均离开 loop thread 且 probe ContextVar 值不变；冻结真实授权和作用域另由邻近生产回归验证。
- Guard worker 暂停期间取消 snapshot，`CancelledError` 传播，fresh host `_history` 未写入；worker 释放后再次 snapshot 从 durable history 恢复同一成功回执 `completed=('a.txt',)`。静态核验 cursor、open_calls、seen、completed 都是局部值/副本，Guard await 取消不会提交缓存 cursor 或破坏原缓存。取消后的只读 worker 可继续，是该 asyncio 原语语义，不是新增持久执行状态。
- 独立执行环境：Windows，本 worktree `.venv310/Scripts/python.exe`，Python **3.10.20**，`PYTHONPATH=src;.`。命令：`python -m unittest tests.test_source_completion_contract tests.test_child_authorization_ceiling tests.test_child_execution_scope -v`。实际 **30 tests / 100.696s，OK，进程 exit 0**；墙钟 **102.759s**。30 项已包含新增两个真实 Guard/ContextVar/取消恢复测试，不重复累计为32。新日志：`standards-path-io-recheck-py310.log`。
- `git diff --check` exit 0；Python 3.10 实际运行无新增兼容问题。原报告的52项回归和旧HEAD整仓3518不冒充本次独立执行数量；本次没有执行全套、Provider 或 P4 外部验收，也没有 commit/push。仅修改本审查记录及新日志。

未消除问题：本次 Standards P2 修复范围未发现未消除 blocking；整仓新候选与 P4 实际验收仍由 Root 独立汇总，不能由本定点 PASS 推导完成。
