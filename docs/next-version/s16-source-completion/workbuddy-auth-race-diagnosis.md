# Windows 3.13 WorkBuddy CI测试竞争诊断

结论：测试生命周期race，不需要改产品。`_auth_task`是当前活动认证任务槽，任务finally清None符合Interfaces“失败保登录/当前模型”和取消生命周期。原测试在submit已让出调度后重新读取可变slot，合法快完成时await None；CI failure与受控probe均同一TypeError。

## 真实证据

CI37616626754 Windows3.13 job112776410037日志：test_workbuddy_switch模块4 tests/1 error；line67 await app._auth_task报object NoneType can't be used in await expression。Root整仓统计767 tests only1 error，本诊断不重跑全量或CI。

链路：set_model→_start_workbuddy_refresh创建refresh task后await sleep(0)；refresh await StoredCredentialSource.resolve（to_thread）后调用discover；当前测试discover为立即raise AsyncMock；refresh_workbuddy_models捕获错误、显示安全失败/重试提示且保当前/登录；refresh finally清slot。若凭据I/O在submit返回前完成，slot已经None。没有要求任务必须持续到submit之后，也不应改产品保留done task来服务测试。

`git diff afbd8f3 f8bb5dc -- src/code_agent/interfaces tests/test_workbuddy_switch.py chaos_agent/auth_runtime_control.py`为空：这些相关生产和测试文件S16候选间未变；测试make_app使用Interfaces独立stub，不运行S16 source gate/child预算。没有S16代码因果证据，CI新候选暴露既有调度假设。

## 实际定点/受控验证

新worktree Python3.13.2 `.venv/Scripts/python.exe -m unittest tests.test_workbuddy_switch.WorkBuddyModelSelectionTests.test_discovery_failure_retains_login_and_a_visible_retry_choice -v`：1/1通过（0.092s）；说明原自然测试依赖调度，不以本地绿否定CI红。

`.venv/Scripts/python.exe docs/next-version/s16-source-completion/diagnose-workbuddy-auth-race.py`：将真实存储凭据resolve边界替为立即完成AsyncMock，仍走原public submit/refresh/finally，原测试确定性复现同CI TypeError；随后仅诊断脚本用相邻success测试的started/release事件屏障、捕获loading task，原失败语义+可见retry input+slot终态None全部通过。共一次red probe、一次proposal green，不循环撞概率，不Provider/HTTP；输出workbuddy-auth-race-probe.log。生产/测试未编辑。

## 建议最小test-only修复（待Root放行）

仅替换失败测试的discover替身：started.set()→await release.wait()→raise ValueError("private")。submit返回先capture loading并assertNotNone；try await started.wait()（沿用邻近3s watchdog），finally release.set()；await captured loading，而不是重新读app._auth_task。之后保留discovery failed/不含private/登录存在/当前profile不变断言，补app.input.text为/model workbuddy:oauth的实际可见retry，以及终态slot None。若等待失败，应finally释放并gather captured task清理。不要新增sleep，不放大wait，不放宽原行为断言，不改生产保留done task。

修复放行后只需该模块4 tests和相关Interfaces认证/运行时切换局部测试；Root另安排新候选CI。当前只交诊断，不改测试、不commit/reset/revert，Root docs staged保持。
