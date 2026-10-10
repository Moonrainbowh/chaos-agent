# WorkBuddy 测试握手独立监督

结论：**PASS，仅此测试修复**。基线候选 `534b84f8e97b7ecfed3f1d9433bf466f68814532`；main `tests/test_workbuddy_switch.py` SHA256 `01ca01486d1927e20060c04f144a8808dfe33da15f103bff8e1b9d0a494f3ceb`。候选未修改。

已读实际 CI `ci-final-ubuntu313-job.log`：唯一 error 在旧测试读取 `_auth_task` 后 await `None`，不是 discovery 失败或 UI 内容不符。生产 `_start_workbuddy_refresh` 在创建任务后 yield，刷新 finally 正确清除当前任务指针并重绘；credential读取含线程池 await，因此结束时间受调度影响。旧 AsyncMock 立即返回不能保证指针仍表示进行中的任务。

独立反例 `workbuddy_independent_probe.py` 保留真实 refresh/credential/界面实现，仅 mock 目录响应。显式等待捕获的实际任务结束后再读取瞬态指针，稳定得到 None；原 `wait_for(None,3)` 必然 TypeError，但模型选择项、加载内容与原 runtime 同时正确。两版本各 1 例实际退出 0，证明已完成刷新清指针是正常行为。

首反例用一次 `sleep(0)` 猜线程池完成，两版本都失败，因为线程池 credential Future 尚未调度完成。原 `workbuddy-independent-immediate310/313.log/.exit` 保留；后续以实际任务完成顺序强制反例，不加等待额度。

已审单文件 diff：用 started/release Events 使 discovery 保持进行中，先读取真实 task、确认非 None/已开始，再放行并 await 固定 task。原 3s task 期限、模型名称、Picker 内容、runtime 不改变断言、其他三条失败/登录保留/二级 Picker 反例均未改变；没有生产或 authentication 源码变更。

独立运行 main 原模块：Python 3.10.20 四例 / 0.604s / exit 0，Python 3.13.2 四例 / 0.350s / exit 0。物证 `workbuddy-independent-gate310/313.log/.exit`。该 PASS 不代替新冻结候选完整 CI 或 S16 最终放行。
