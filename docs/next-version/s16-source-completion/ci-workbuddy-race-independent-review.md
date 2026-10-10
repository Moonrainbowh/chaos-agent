# CI WorkBuddy race独审

结论：**PASS_TEST_ONLY_FIX，可放行新候选CI**。没有发现修改产品或隐藏生产错误的证据；此结论不将旧CI红标绿，也不代表新五平台已经通过。

## 实际核对

读取实际test diff、诊断脚本/历史probe、作者报告和313/310工具输出摘录，核对production `_start_workbuddy_refresh`：create_task后sleep(0)，refresh finally若仍是当前Task便将活动slot清None。这是合法完成行为，submit不承诺返回时slot仍非空。原测试在submit返回后重新读取slot并await，快完成时正好得到None。受控立即credential resolve的历史probe从public submit复现同CI TypeError；自然本地绿不能否定平台竞态。

独立运行`git diff afbd8f3 f8bb5dc -- src/code_agent/interfaces tests/test_workbuddy_switch.py chaos_agent/auth_runtime_control.py`为空；当前相关生产工作树diff也为空。测试make_app不经过S16 source gate，没有把S16行为回归错误转嫁为等待时间的问题。

修复仅失败用例：discover通过started/release暂缓其预定ValueError，测试在释放之前捕获真实Task引用，finally释放并等待同一Task。没有条件跳过await，没有新增sleep/延长等待，没有改变生产finally、异常捕获或credential行为。邻近成功用例已有同类3秒watchdog。原discovery failed、private不可见、登录保留、当前profile不变四项断言全部保留；增加可见retry输入及slot清None检查，因此不是绕过错误或减少断言。

## 验证边界

- 作者局部3.13.2：四模块19/19、2.097s；3.10：同19/19、3.711s。ci-workbuddy-race-313.log/310.log明确为工具结果末尾摘录，未当完整stdout或独立重跑。
- 本审查独立运行本worktree.venv `python -m unittest tests.test_workbuddy_switch.WorkBuddyModelSelectionTests.test_discovery_failure_retains_login_and_a_visible_retry_choice -v`，实际1 test/0.081s，OK，进程exit0。完整单例输出保存在ci-workbuddy-independent-single.log。
- test范围git diff --check通过。旧diagnose脚本依赖旧test方法，当前更新后不能原样再跑其中预期旧红段；历史已执行probe作为修前证据保留，不虚称现脚本仍可复现旧源码。

仅新增本独审报告/单例日志；未改产品、测试、harness、owned或Root staged内容，未commit/reset，未全套、Host、Provider/P4或CI重跑。Root可提交新test候选并按新SHA运行五平台CI，保留旧候选唯一error与全部原日志。
