# S12 history 异步换 run 快照修复

独立监督确定的合法竞态：history 捕获旧 finished run 后，在结果读取或分页 await 期间，同 Task 恢复 RUNNING 并替换 `_runs/_active`；旧实现能返回 `task.running + result.waiting_decision + active_task=None`。独立探针与第五失败物证保留不变。

新增标准 Event 屏障回归先红：`history-replacement-red.log`，结果读取和 message_page 两项都失败；不使用 sleep、付费模型或默认库。修复仅 RemoteTaskController Unit 和邻近两个测试文件。

`history` 的私有只读 snapshot 在 snapshot/reconcile/task/page/condition/result 每个异步边界之后检查同 session 的当前 run 对象身份。变更则丢弃整个旧读投影，重新取得最新 run、Task、message_page 的 maximum/base sequence；不会沿用旧 active_task/cursor/result，也不会重放 Task 动作。无 local run 时也同样核身份，能恢复读取中出现的新 run。活动 run 另外核 task_id/thread_id。

history 初始 result 恒为 None；只在 Task 非活动、run 非 live 时，从精确公共 Foreground.result + Task.updated_at 二次校验填持久结果。即使旧 finished 对象未换、另一入口已把 durable Task 恢复为 RUNNING，也不附旧 waiting result、不伪造本地活跃 runner。查询旧会话不替换另一活动会话。

三个连续 snapshot 都在读取中变化时，返回既有 RemoteConflict/HTTP409，明确表示没有得到稳定快照；这个界限只约束只读投影，不重试批准/模型/动作。PWA 邻近回归真实页面脚本接409后保留 device credential 和 historyPending，下一次 sync 能重读成功，不误401。HTML 和审批协议未改。

最终验证见 `history-replacement-focused.log`（9 tests /1.647s/OK）、`history-replacement-pwa.log`（Node行为全PASS）、`history-replacement-remote-final.log`（89 /32.623s/OK，1 skipped）与 `history-replacement-integration-final.log`（6 /13.066s/OK）。Remote/Root 最终日志以本轮最终 initial-result=None 行为快照执行；此前88项/Root6日志保留为中间物证。

规则没有本轮修改，原契约已包含不跨run/live、持久快照与cursor要求；生产八链仍通过，Remote5999、Interfaces6000、Sessions5987，6000/20000未变，`history-replacement-rules.log`。

精确本轮3文件/hash是 `history-replacement-files.json`：task_controller.py、test_status_reconciliation.py、test_pwa.js。主 Agent 负责新候选冻结/full30/独立监督；本轮未改候选、旧manifest、authentication用户文件、真实用户DB/凭据，未push。
