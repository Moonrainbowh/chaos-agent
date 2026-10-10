# 全量检查异常诊断

原始记录：[full-tests.log](full-tests.log)。本次不修改下列测试、源码、时限或跳过条件；失败记录不因后续复跑通过而删除。

## MCP 启动期限

`test_fault_lifecycle.RealFaultLifecycleTests.test_busy_rejection_preserves_already_approved_queue_and_generation`
在 `controller.enable("fixture")` 的首次握手阶段报 `TimeoutError`，尚未执行队列繁忙与 generation 断言。
独立子 agent 核对：夹具的 `start_timeout_s=1.5` 包含真实子进程创建、FastMCP 导入、initialize 和工具发现。
栈停于 `OfficialMcpSdkAdapter.start()` 的 `session.initialize()`，由生命周期 owner 的期限取消。

该用例只装配 MCP Controller、Manager 和 Adapter，不经过本次父复核 Host 装配。MCP 源码、夹具、依赖与运行器均与基线相同。日志没有各启动阶段耗时或子进程 stderr，因此目前只能称为未归因的启动超时，不能直接归因于机器负载、杀毒软件或本次补丁。

## 运行时夹具身份记录

`test_local_process.WindowsLocalProcessTests.test_timeout_terminates_descendant_before_delayed_marker`
在读取 `descendant-identity.txt` 后按 `|` 拆分时得到单个字段，报 `ValueError`，尚未进入后代进程存活断言。
运行时及该测试与基线相同，不经过父复核流程。

夹具在真实父进程启动后写 `pid|create_time`，同时运行时固定在 0.3 秒终止该进程。
测试只区分文件存在和不存在，没有区分创建了文件但内容尚未完整写入的状态。
日志与写入被期限打断的竞态一致，但没有保留文件实际内容或分阶段时间，故这一解释是源码支持的推断，不能宣称已经证明具体调度原因。

## 复跑

首次全量结束后，使用同一解释器、原有时限和断言，依次原样复跑 MCP 与 Runtime 套件：

```text
.venv/Scripts/python.exe -m unittest discover -s src/code_agent/mcp/tests -p test_*.py -v
.venv/Scripts/python.exe -m unittest discover -s src/code_agent/runtime/tests -p test_*.py -v
```

- MCP：33 项 / 24.312 秒 / OK，退出码 0，见 [mcp-rerun.log](mcp-rerun.log)。
- Runtime：131 项 / 24.576 秒 / OK (skipped=5)，退出码 0，见 [runtime-rerun.log](runtime-rerun.log)。
- 原报错的两个具体用例在复跑日志中均为 `ok`。没有增加等待、放宽超时、调整断言或新增跳过，也没有改动相关源码。

复跑表明本次未再次出现这两个错误，不证明具体环境原因，也不能替换首次全量失败事实。

## 容量夹具的另外两个错误

首次全量还发现 `tests/test_persistent_request_capacity.py` 的两个错误。干净基线同样复现，原因和局部夹具修复见 [capacity-regression.md](capacity-regression.md)。它们与上述启动超时及身份记录问题分别处理；修后原两项通过，生产容量上限不变。
