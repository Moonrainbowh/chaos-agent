# S12 真实应用到 HTTP 审批接线验证

新增标准 Root 集成测试 `tests/test_remote_approval_integration.py`，没有继承旧测试类。最初4项验证实际 `create_application`、TaskScopedDispatcher → RootActionDispatcher → permission_dispatch → 中央持久 ApprovalBroker → ASGI HTTP GET/POST → 原 Workspace 写路径；没有替换Broker或策略执行器。Root补公开 `Application.approvals` property 后可由远程取得 dispatcher 原共享实例。

所有 DB/state/permission rules/workspaces/UserProfile/LOCALAPPDATA/ProjectStore/PairingStore均明确落在TemporaryDirectory，构造前断言绝对路径且在Temp根内。配置已知 gpt-4.1/offline provider；前4项NoProviderCalls若被使用即失败，第5项仅注入明确ScriptModel，不调用付费或联网Provider。不构造默认用户库，不打开 live Host、公网、凭据文件。测试中 `.env` 是Temp内固定fixture占位，显式允许 sensitive paths仅用于该fixture。

普通冻结Task已授权本地写可由原policy直接ALLOW。测试选择原policy的真实PROTECTED_PATH ASK，验证需要审批的分支，不把全局写权限改成每次审批。typed Task/ActionExecutionContext/实际owner/workspace由真实应用持久组合。

最终实际结果：**4 tests / 3.385s / OK**，日志 `integration-approval-final.log`。

- 审批卡到达前后文件仍不存在；HTTP批准后原dispatcher成功写固定内容，Temp库completed workspace_mutation回执恰1条。
- HTTP重复回答409；文件mtime和已完成回执数量不变，未重复执行。
- HTTP拒绝200并持久denied，原ActionResult为错误，文件不生成。
- 跨任务回答404、错误state_version409，原waiter不唤醒；随后合法当前回答仍成功。
- 真实TaskState generation/subject漂移回答409、设备revoke后回答401，文件不生成，未释放旧waiter；收尾取消并等待清理。

首轮4error暴露真实Application DTO未公开Broker，Root已补单一来源property，日志 `integration-approval-initial.log` 保留。补“执行一次”的原始SQLite计数后，第一次只用connection事务上下文导致Windows临时库句柄未关闭、清理WinError32；已改为显式closing，两次读取均构造前重新assertTemp。失败日志 `integration-approval-count-cleanup-failed.log` 保留；最终通过包含完整Temp清理。

前4项证明真实内部接线及ASGI请求的审批/物理副作用边界；不证明模型端工具生成或完整Foreground模型回合（第5项补充如下），均不证明真实手机、浏览器TLS信任链、公网代理或Host部署。真实网络/真机仍由Root单独验收。

## 完整 Foreground 回归补充

针对Restricted wrapper曾丢失Host敏感路径能力的真实集成回归，新增第5项：先显式退役手工Task，通过真实HTTP项目目录创建新Session、POST messages进入原Foreground；固定ScriptModel先请求.env写，再输出收尾文本。真实HTTP读取审批卡并批准，等待原Remote runner，不修改model生成之外的状态机或跳过structuredverification。实际文件为`S12_FOREGROUND_FIXTURE_ONLY`；2个模型轮、TaskState generation=1且subject非空，原completed mutation回执恰1条。旧Guard能力丢失会在真实写后snapshot路径失败，此测试由实际文件/持久generation/真实生命周期判定，非镜像字段断言。

最终实际返回：`completed / verified`，仅1条`system_planner`完成证据；无系统verifier进程证明、无真实Provider或真机证明。测试仅接受Host typed事实，若该风险计划转为waiting_decision则通过HTTP明确接受部分交付并保持非verified；已completed时不强迫非法partial决定。初次新测试硬要求waiting_decision失败，日志integration-foreground-initial.log保留；随后按真实Host结果与Planner provenance核对。

最新新进程完整 **5 tests / 6.579s / OK**，日志integration-foreground-final.log含`S12_FOREGROUND_RESULT`有界事实。structuredverification明确开启；模型Fake仅替换原ModelHolder.model，TaskScoped/Restricted/Root与Verifier实例、仓储、Broker和HTTP层均真实。此项覆盖完整Foreground的离线模型回合，仍不证明真实Provider行为。

原4项source/rawhash/log分别保留于integration-four-tests-source.py、integration-four-tests-files.json、integration-four-tests.log。当前5项rawhash更新在integration-files.json，测试编辑已停止，可进入第三候选snapshot。

新增产品范围仅测试文件；raw SHA256清单见 `integration-files.json`。相关真实Application的S11项目记忆入口回归 **3 tests / 13.328s / OK**，日志 `integration-application-regression.log`；完整候选/标准套件由主Agent继续核验。
