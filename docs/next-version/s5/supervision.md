# S5 独立监督

最终结论：PASS。S5 可标记 DONE 并进入 S6。首 yield 生命周期回归已最小修复并独立验证，最新冻结候选全量通过，无未解决的 S5 阻断问题。

审查范围：规划 S5 的持久未决恢复门、单独操作者核对、本地可信回执及未知结果处理。修后冻结候选 23 文件，差异 SHA-256 `ae14cb68ed7ee2be4f5dc87d6e2f976fb3cc99dec83cd988b1495ac56733c85b`，基线 tree `8999121dcaa43b925b6b84702f25cc52046e2501`。独立监督未改产品源码、快照或实施自测数据。

独立验证均在候选锁定 CPython 3.13 环境执行：

- Core 全套 165 项通过，`supervision-core.log` 保留发现数和运行数；S5 Core/Sessions/Interfaces/Host 相关 18 项通过，`supervision-related.log`。其中真实子 Python 进程恢复、旧 call ID 在写入新 assistant 历史之前拒绝、新 ID 可继续、暂停但动作未退出期间 owner 阻断、finally instance CAS、过期/错任务/错动作/错序号/重复核对、人工来源反馈及验证状态失效均通过。
- 独立脚本 `supervision_probe.py` 自建 TEMP SQLite，累计消耗 2 个模型回合、3 次工具调用和 48 tokens；连续与跨 resume/events/resume_thread 入口、数据库重新打开共 4 次恢复均零 runner 调用。缺可信回执、过期版本、错误动作与序号、未授权、重复决定均拒绝。唯一可信只读原事件回执合法补齐丢失反馈，后续恢复可调用 runner；核对期间不执行原动作、不产生验证证据，已消耗预算及契约保持。结果在 `supervision-probe-result.json`。
- 独立 `supervision_host_probe.py` 使用生产应用装配及真实 mutation capture；分别替换可信观察返回值的操作、owner、workspace fingerprint，三种异源回执均零持久写入拒绝。真实 completed mutation 可闭合未决记录且不重放。结果在 `supervision-host-result.json`。
- 上述 Host 脚本独立逐文件核对主/候选 23 项 SHA、补丁 SHA，并验证用户原 authentication 三文件 diff SHA 保持 `7cbeb87002763fbe5de0bf33a4fd68193af6d66e5340be75f2a1edeb776befca`。

源码核验：Core/Foreground 都从持久消息检查而非状态名；配对按原 ID/name/消息顺序，重复 ID 保守阻断。解析或核对不执行工具。决定在同一数据库事务内复核 task/thread/call/message sequence 与持久版本；真实目录身份、当前字节 SHA、operation/owner 在 Host mutation gate 下核对。活 owner 拒绝核对，旧 finally 不删除新实例。人工报告保留 `is_error=true/verified=false`，不冒认工具成功；人工/本地核对推进内容代际并清除旧当前验证事实，不删除历史证据。

边界：S7 将处理 register 覆盖活 owner 的完整执行归属问题；本 S5 已阻断尚未退出且有未决记录的动作核对。CLI 纯历史不依赖 Provider/Shell 的装配解耦属于 S8，手机响应适配与真机验收属于 S12。当前证据不宣称外部 exactly-once、跨平台 CI 或真机完成。

初次请求改动及失败物证：`all-tests-final.log` 最后总摘要为 `exit_code=124/failed_suites=1`；29 个 Feature/remote 套件通过，根 `tests` 未完成，未填充全仓总数。超时前出现 `..F`，600 秒后最后活动测试为 `test_workspace_checkpoint_lifecycle.ForegroundBarrierTests.test_first_yield_quiesce_waits_for_token_and_subagent_release`，栈停在 `IsolatedAsyncioTestCase` teardown 的 asyncio runner.close。独立单跑 `tests.test_workspace_checkpoint_compatibility` 1 项 2.937s PASS，只排除了前一个场景自身的独立失败，没有替代根全套或掩盖 hang。

该请求的修复复核：`task_controller.events` 只把 token 安装恢复到原首 yield 后时点；owner 仍在首 yield 前注册且 try/finally 覆盖 yield 与 instance CAS 释放。原 checkpoint 屏障依赖 token 生成后取消的时点，保留 `assertNotIn(task.id, controller._tokens)`。独立复跑原 lifecycle 与 Sessions/Interfaces/Host 相关共 20 项，7.969s 全通过（`supervision-first-yield.log`）。自建 `supervision_first_yield_probe.py` 验证首 yield 后 token 不存在、owner 存在，立即 aclose 能释放本实例且零 runner 调用。Host 异源反例及新 23 文件/补丁/auth SHA 再次独立通过。初次全量超时结果保留。

最终全量证据核验：`all-tests-repaired.log` 最后一项根套件为 590 项、220.286s、fail/error/skip 为 0；最后 `CHAOS_TEST_SUMMARY` 为 30 套件、3067 发现且运行、30 平台跳过、fail/error/unexpected success 为 0、unrun 为 []、exit_code=0。独立监督直接读取该完成摘要，未使用初始候选数量代替。保持原 600 秒 timeout，未跳过屏障测试。最终通过后再次执行独立 Host/哈希脚本，23 文件主/候选与补丁 SHA 一致、用户原 auth diff 未变。因此关闭本次 CHANGES_REQUESTED。

复跑曾遇监督脚本的非产品问题：标准 unittest `-t src` 不适用于未带 `__init__.py` 的测试目录，改用已审项目 `scripts/run_test_suite.py` 后 Core 165 项完整通过；构造异源 coverage 必须传合法 64 位 SHA 值，修正监督输入后全部反例通过。未据此改产品或削弱断言。
