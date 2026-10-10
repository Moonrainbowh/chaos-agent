# P3 path I/O 局部修复报告

状态：IMPLEMENTED_PENDING_STANDARDS_RECHECK。固定98f9a74整仓测试结束后Root放行，按p3-path-io-repair-plan实施；当前生产改动已冻结，无运行中命令。未Provider、prepare/preflight、commit/push或重复整仓全套。

## 改动范围

- chaos_agent/child_runner.py：启动canonical_sources通过asyncio.to_thread执行，仍先核验再创建子线程和绑定来源。
- chaos_agent/source_completion.py：snapshot supplied规范化、历史配对_full_read通过asyncio.to_thread执行。原WorkspacePathGuard、dispatcher/插件身份判断、配对/完整回执和持久要求比较均保留。线程只返回路径事实；snapshot局部集合及缓存仍由loop维护，取消不吞掉也不提交局部cursor。
- tests/test_source_completion_contract.py：新增两个定点测试。包装真实WorkspacePathGuard构造（调用原Guard），核验启动、fresh history和supplied三种路径不在loop thread，ContextVar完整复制；取消暂停worker期间snapshot后再次从durable history获取同一成功来源。
- tests/test_s16_source_completion.py：仅删除Root指出的EOF一个多余空行。

没有改Core/Sessions/预算/事务/信任契约，既有AGENTS.python.md要求已经覆盖异步I/O规则。其他docs脚本与进度变动由Root/P4作者维护，不属于此次生产修复。

## 红绿物证

使用本worktree `.venv/Scripts/python.exe`，PYTHONPATH=src;.：

1. 红：两个新增定点测试，2 tests / 3.459s，failures=2。真实Guard观测到loop thread；取消回执测试明确抛出source Guard blocked the event loop。日志p3-path-io-red.log。未用固定睡眠模拟通过。
2. 三调用点修复后定点绿：同2 tests / 3.552s，OK。日志p3-path-io-green.log。
3. 聚焦邻近：`python -m unittest tests.test_source_completion_contract tests.test_s16_source_completion tests.test_production_child_factory tests.test_child_result_contract tests.test_child_authorization_ceiling tests.test_child_execution_scope tests.test_runtime_partial_build_cleanup -v`，52 tests / 103.322s，OK，实际进程exit0。日志p3-path-io-regression.log。该52包含上述新增2，不将重跑叠加为54个唯一用例。
4. git diff --check通过；本报告存在已核验。原始日志空格/失败物证未清理。

## 取消与恢复说明

to_thread复制当前ContextVars，因此生产受限路由仍使用原冻结上下文。cancelled await可能留下只读worker继续执行，这与asyncio原语一致；worker不修改历史缓存/ledger。被取消snapshot仅修改尚未提交的local open_calls/completed，下一次snapshot从原durable cursor完整配对回执。测试验证取消传播、host cache未提交及再次completed=('a.txt',)。原source cancellation/epoch/预算优先/名字伪造等反例及公开生产子链路均在52项回归中通过。

98f9a74全套3518由Root独立执行，本报告不把旧SHA全套计为新未提交修复的全套。当前修复等待Standards复核与Root最终新候选检查。
