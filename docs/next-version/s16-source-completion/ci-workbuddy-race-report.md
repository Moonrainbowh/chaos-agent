# CI WorkBuddy测试竞争修复

结论：test-only修复已经完成，Python3.13/3.10各19/19局部通过；生产runtime源码与f8bb5dc完全一致。需Root独审后提交新测试候选并重新五平台CI；P4真实质量FAIL不重跑、不重标。

## 根因及红证据

CI37616626754 Windows3.13 job112776410037：test_workbuddy_switch 4tests/1error，line67 await app._auth_task发生NoneType TypeError。_start_workbuddy_refresh create_task后await sleep(0)，任务finally将活动slot清None，符合原生命周期；凭据resolve快完成时，submit返回前即可合法清slot。测试重新读取mutable slot的假设错误，不是需要保留done task的产品缺陷。

相关baseline核验：git diff afbd8f3 f8bb5dc -- src/code_agent/interfaces tests/test_workbuddy_switch.py chaos_agent/auth_runtime_control.py为空；S16未改相关UI生产与此旧测试。另git diff f8bb5dc --exit-code -- src chaos_agent退出0，当前产品与f8真实字节一致。测试独立make_app stub不走S16 source gate。没有S16生产因果证据。

本地原自然single case1/1绿（0.092s）没有否定CI红。docs diagnose-workbuddy-auth-race.py在修复前对StoredCredentialSource.resolve使用立即完成AsyncMock，在原public submit路径确定性复现同一TypeError；事件屏障/captured task proposal保原语义与retry/slot None通过，workbuddy-auth-race-probe.log保留。该诊断脚本调用旧测试方法，测试修后不能原样重跑其“预期旧红”段，作为已执行历史物证保留，不伪称现脚本可重现旧源码。

## 最小改动

仅tests/test_workbuddy_switch.py失败用例：discover替身started.set→await release→raise私密错误；submit后capture loading并assertNotNone；等待started在finally中释放并await同一真实Task，避免重新读mutable slot。所有原业务断言保留，新增可见retry输入及终态slot None。沿用相邻case3s watchdog，不增加sleep或wait，不条件跳过await、不修改产品cleanup。

## 实际验证

在新worktree执行：

- `.venv/Scripts/python.exe -m unittest tests.test_workbuddy_switch tests.test_auth_runtime_control tests.test_tui_auth_integration code_agent.interfaces.tests.test_tui_auth_commands -v`：19/19通过，2.097s，exit0；Python3.13.2。
- `.venv310/Scripts/python.exe -m unittest`同4模块同参数：19/19通过，3.711s，exit0；Python3.10。
- git diff --check通过；production against f8 diff退出0。

两个ci-workbuddy-race-313.log/310.log是实际工具输出末尾摘录（非完整stdout/非重跑记录）。模块组成：WorkBuddy4、AuthRuntime9、TUIAuthIntegration1、InterfacesAuthCommands5。没有全套、CI rerun、Provider/HTTP请求。仅新文档物证及单失败测试修改；未commit/reset/revert，Root staged docs保持。
