# S14 首次全套 MCP 启动超时诊断

范围：只读代码和诊断脚本；未改 MCP 产品、测试、启动/调用/关闭期限，未预加载 SDK 绕过实际测试的启动预算，未使用自动重试。无 Provider/默认数据库访问。

## 事实与结论
- `all-tests.log` 首次标准全套的 MCP 27 例中 1 error：第一个 busy queue 用例在 `controller.enable -> LifecycleOwner.run -> OfficialMcpSdkAdapter.start -> ClientSession.initialize` 收到启动 1.5s deadline。owner 同步段被 asyncio 记录为 1.100s；因此该次全套仍是失败，不能因为诊断通过改写。
- 该 deadline 包含首次官方 SDK 同步 import、Windows 子进程建立、initialize 和工具发现，而测试 fixture 子进程还需首次 import FastMCP 才能响应 initialize。失败发生在首个 initialize，尚未触发 queue operation，不能归因于 busy queue 行为。
- 不改变期限的实际目标用例测量：冷 parent SDK import 0.311413s，initialize 0.415815s，adapter ready 共0.742720s；同进程热 parent import约3–4微秒，initialize 0.401501–0.420695s。3 例实际通过，日志和 JSON 已保存。与首次全套 1.100s 同步段相比，现时冷加载明显更快。首次日志没有内部 import profiler，无法把1.100s全量严格分解到模块/磁盘/调度，因此不宣称已证明具体硬件、杀软或负载根因。
- Windows运行的是候选 `.venv/Scripts/python.exe`，`sys._base_executable` 为 `D:/soft/miniconda3/python.exe`。SDK拥有的 process PID 已在测量中记录；fixture 通过显式 `TemporaryDirectory(prefix="s13-mcp-")` 的 PID 标记和确切 root 绑定验证退出。未枚举或终止无关进程。
- 同一候选原代码、原 1.5s 期限、无 instrumentation 的完整 MCP 27 例另一次诊断运行：actual exit0，27 run，0skip，0fail/0error，18.479s。真实子进程关闭/启动超时/短关闭deadline/unknown-write禁止重放/撤销和代际队列/繁忙拒绝均由原完整用例覆盖；每个 RealFaultLifecycleTests 的 teardown 经 SDK aclose 后验证自有 child 已退出。
- 未发现 S14 新引入的 MCP 正确性问题；现有事实支持启动预算因首次同步启动成本被消耗，而非队列逻辑失败。诊断27通过只说明本次范围，不能替代 Root 修复后冻结候选的标准全套最终物证。

## 物证
- `mcp-startup-timing.py/.log/.json`：只读真实目标冷/热测量，3例通过。
- `mcp-unmodified-suite-diagnostic.log`：候选源码完整27例，实际exit0。
- `mcp-startup-timing-instrumentation-error.log`：首次诊断探针的 fromlist=None 处理错误，属于探针错误，已修正；不能算产品失败或验收通过。

不建议通过增加 timeout、skip、预加载或无限重跑处理。若新冻结标准全套再次在 initialize 超时，应保存那次真实失败并进一步测量启动阶段，而不能仅拿本次诊断通过覆盖。
