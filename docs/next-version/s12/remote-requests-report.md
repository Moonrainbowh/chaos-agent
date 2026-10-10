# S12 Remote 请求与决定 Unit

Unit 实现完成，产品编辑停止。仅新增 requests.py/邻近测试，TaskController加两个公开lookup，Foreground加已提交部分接受结果投影；未改server/static/AGENTS/transport/pairing/Root组合。网络与真机仍需Root验收。

`RemoteRequestControl(tasks).pending(session_id)` 返回 `{task_id, session_id, requests:[flat ledger records]}`，100条上限，最新卡优先。同控制器刷新串行，同绑定操作复用pending卡。approval来自同中央仓储，不消费Broker/TUI队列。decision.preview.operation固定continue/accept_partial/stop/reconcile；最后一种由preview.reconciliation固定call_id/message_sequence/recovery version/窄decision，客户端不能重述或切换operation。未知调用最多16条生成对应核对卡，继续与部分接受关闭，其他原调用仍保持S5闸门。

`respond(task_id, body, authenticate)` body为request_id/action_digest/state_version/owner_instance_id/approved，以及reconcile批准时必需reason/evidence；额外字段拒绝，路径UUID由server补进body。approve委托Application.approvals.respond；decision使用同ledger CAS、TTL、版和owner检查，accept_partial/stop仅WAITING_DECISION通过仓储原子transition。consume首次consumed_now才执行对应Host控制，重复不调runner或核对。继续使用原TaskController.start/Foreground和单执行槽；S5核对委托生产Foreground.resolve_pending_action，不执行原动作、不自动继续，人工报告unverified。

`RemoteTaskController.request_task(session_id)` 从持久catalog选精确Task；`request_application(task_id)`核task/thread/project，复用仍属于当前RemoteApplications的primary/child运行应用。另一任务活动时拒构造/关闭其项目应用；空闲才通过原应用工厂组合已知项目，不接受客户端root/profile/命令。

`ForegroundTaskController.record_accepted_partial_result(task_id, expected_updated_at, prior_result)` 只读核ACCEPTED_PARTIAL与规范化UTC时间戳，再复用原TaskResult记录，不transition或伪造验证。真实SQL时间Z与对象+00:00通过标准datetime比较，首轮差异失败日志保留。

验证：新增10个真实显式TemporaryDirectory SQLite+FakeForeground公开API测试全部PASS（requests-focused-final.log，3.276s）；原durable TaskResult7个回归PASS（requests-result-regression.log，2.267s）。Remote完整套件71run/1skip/1fail；唯一失败已交Root修复其并行PWA接线：未知归属session原只读提示被待办请求错误覆盖，test_pwa.js模拟fetch不识别新requests端点。Python subprocess GBK解码Node中文stderr另报UnicodeDecodeError；直接运行Node确认实际业务断言失败。requests-remote-suite.log保留，不把该套件报成功。

消费与实际continue/S5核对/结果投影是不同服务调用。服务失败或进程中断不重放已消费动作：持久卡保留响应，下一快照根据当前Task/未决事实提供新显式操作，S5未知闸门不变。设备StoreLock跨进程消费/撤销线性化由Root的认证适配持有，不由本Unit独立保证。真实手机/HTTPS入口验收仍未完成。

4个产品路径及raw SHA256见 remote-requests-files.json；无默认Repository、Provider或live Host操作。

Root修复其PWA中间态后，最终重跑完整Remote套件：**71 tests / 23.984s / OK (skipped=1)**，物证 `requests-remote-suite-final.log`。初始失败日志保持；本Unit4个产品路径未因该UI修复改变。
