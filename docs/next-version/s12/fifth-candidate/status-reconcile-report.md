# S12 持久决定后 Remote 状态恢复

## 问题、范围与结果

实际 RootApplication/离线模型/HTTP 测试确认：MODIFY 写被拒绝、随后中央 consume accept_partial 已提交，Task/history 是 accepted_partial，而 `/status` 一直返回旧 RemoteRun 的 waiting_decision。这是确定产品缺口；先前真实手机旧标签的旧 DOM 解释不免除该状态恢复问题。旧 doc 探针及失败日志全部保留。

仅修 Remote 状态投影与 PWA 快照显示，不改变审批卡/response JSON、权限、CAS/owner/TTL、模型循环或决定执行。POST 已消费但响应丢失后，GET 状态和持久 history 可以恢复，不要求客户端再批准。

## 最小实现

`RemoteTaskController.status` 原本已是 async，签名不变。新增私有 `_reconcile_finished_run` 只读 finished run 的精确 task_id/thread_id，活动状态拒投影；读 Foreground 公有 result 后再次核 Task.updated_at 和同 session 的 run 对象身份，防止 await 中新 run 被旧结果覆盖。若 durable 决定已转终态但 result 落账未完成，使用同 Task 的未知验证投影，不把旧验证升级。status 在 await 后再核 `_active` 对象，切换时读当前 run。

history 的 `result` 是新增只读 DTO 字段，同 Task/version 检查；无旧 run 时通过原 Foreground.result 从共享 Repository 读取，不切项目或创建/关闭 application。正在执行的另一个会话不被历史查询替换。

PWA 新增按当前选中会话存储的 selectedResult，历史仅更新选中结果而非 globalRun。poll 看到同选中会话从 waiting_decision 变 accepted_partial 等非活动状态时，重新加载持久 history；即使 POST 回复丢失仍恢复。已有 viewEpoch/historyRevision/WS 身份过滤保留。

## 验证物证

- 先红：`status-reconcile-red.log`，真实 HTTP after_status waiting_decision、history accepted_partial，VM 验证标签缺失断言失败。
- 最终真实 Root 集成：6 tests /9.101s/OK，`status-reconcile-integration-final.log`。新增一项在默认 discovery，真实 create_application/Temp SQLite/显式 ProjectStore+PairingStore，构造前 assert；实际固定 MODIFY、原工具 ASK、HTTP拒绝、完整runner两轮、中央HTTP accept_partial，无文件变化。
- 同一真实 HTTP 前后 snapshots 喂给实际页面 Node VM：回应后、poll、新 VM、消费后回复丢失/再poll，均显示“已接受部分 · 未验证”；查看旧终态会话时另一个 active run/banner 保持。未声称为新的真机验证。
- 邻近 5 项 Temp SQLite 回归 /0.760s/OK，`status-reconcile-focused.log`：持久终态恢复；await 中同 Task 换 run 不覆盖；另一会话 active 不中断；thread mismatch 不读/拷结果；清除旧 run 后历史仍读同 Task。
- Remote 完整套件 85 /27.816s/OK，1 skipped，`status-reconcile-remote.log`；之后仅增加 history.updated_at 二次检查，最终上述 focused 5 和 Root 6 已覆盖该最终行，全标准冻结由主 Agent执行。
- PWA 全行为检查通过，`status-reconcile-pwa.log`。新增 Node helper 的初次 other-run 检查暴露 fixture 自动首次 sync 仍在途，增加等待初始 sync 的 barrier 后通过；不是增加睡眠/跳过检查。
- 最终生产规则八链通过，Remote 5999、Interfaces 6000、Sessions 5987，6000/20000预算未变；`status-reconcile-rules.log` 与 `rule-chains.json`。

## 规则压缩映射

新增仅 TC Unit 的同 Task/version 持久投影、拒跨 run/live、丢回应 poll。此前 mandatory 边界都仍在本契约：配对/单设备/单槽；原 Application/Task/Sessions；归属/终态保留/重启；ProjectStore 与只读历史；central bindings/TTL/CAS/consumed_once/未知S5；401vs403；HTTPS/具体私网调试；先提交批准/后撤销不回滚；StoreLock/原子保存/fsync/取消释放；有界事件/固定run/cursor/gap/epoch；敏感内容不泄；PWA确认/草稿/HTML/旧请求/离线不自动批准。缩短同义文字与标题，没有移出强制约束。

精确本次 7 文件 SHA256 清单为 `status-reconcile-files.json`。新文件是 `remote/tests/test_status_reconciliation.py`、`remote/tests/decision_snapshot_pwa.js`；根集成原文件加一项测试，JS 原测试 helper 导出只在 require 情境跳过自运行，标准 `node test_pwa.js` 仍完整执行。没有改原 authentication 文件、候选、旧 manifest、真实用户库/设备凭据，没有 push。
