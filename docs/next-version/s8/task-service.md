# S8 共享任务入口与 ACP 实施

状态：局部实现与验证通过，待主 Agent 集成全量验证和独立监督；不代表 S8 放行。

`Application.tasks` 返回原 `foreground_tasks` 同一对象。`interfaces.task_service.TaskService` 只声明 start/events/pause/interrupt/accept_partial/recovery/resolve/result；继续执行就是 events，生命周期仍由现有 IntegratedForegroundTaskController/Core/Sessions 完成，未新增 Agent loop/调度器/Task schema。

生产 `serve_acp` 注入同一个 AcpTaskService 作为 prompt 与会话历史桥。ACP Session ID 固定为原 root thread；`acp-session-selection` checkpoint 明确记录实际 Task/thread/source root。空新会话绑定首个 Task，暂停/中断恢复原 Task 与冻结契约/累计预算，终态续聊复制消息到新 thread/新 Task。load/list 不用全局 UI 当前状态或最近 sibling 猜选中的任务；投影核 root relation、Task.thread、source lineage/workspace、非 child budget binding。无可信项目来源的旧 taskless 历史拒绝执行。CLI taskless resume 空线程创建实际 Task，已有正文且缺来源明确拒绝。

客户端 token 传入 Base/Integrated events；只有显式传 token 的入口使用有界16事件桥。一个消费 task 连续执行原 controller stream，保证 ContextVar 连续；token 取消可打断阻塞 Provider await，清理消费 task 后才进行父子 settle/owner release。SDK 外层 asyncioTask.cancel 会取消 token、持久 INTERRUPTED 与真实 cancelled result；已完成 Task 的迟到取消保留完成事实。未知 usage 保留 pending reservation；未知 action 恢复门返回 refusal，不分配新 Task/调用 Provider。TASK_RESULT 决定 ACP stopReason，模型 COMPLETED 不能覆盖持久拒绝/中断。

验证使用候选 locked CPython3.13，PYTHONPATH 指向主工作区；未修改候选、未运行 live Host 或真实 Provider。

- 组合验证45/0（17.880s）：`tests.test_acp_task_service`13、transport兼容4、S7真实 child scope3、Interfaces task controller14、ACP adapter11。日志 `docs/next-version/s8/acp-task-service-tests.log`。门控客户端证明 Task 已实际 COMPLETED、结果还未递送时取消不会追加 cancelled 假事实或覆盖完成；真实取消不同步复活终态。
- 独立 ACP Feature11/0 与 TaskController14/0 日志 `acp-feature-tests.log`、`task-controller-tests.log`。
- 新真实 Application/Core/SQLite 场景覆盖首 prompt/计费、终态 continuation stable ID、数据库重开 projection、暂停同 Task/契约、token取消阻塞Provider并释owner、SDKTask.cancel持久中断、完成后迟到cancel、missingusage保未知责任、跨项目/非法relation拒绝、childbinding拒绝、unknown action阻Provider、公开 serve_acp 装配，以及CLI taskless路径。

首轮两项错误属于测试字段名错误及 Provider fixture 阻塞揭示的取消接线缺口；已修复实际流取消路径。曾尝试逐 anext task 的实现破坏 ContextVar 连续性，已移除改为单消费task。公开serve测试最初输入 `Explain` 不触发分析意图，因此如实 refusal；调整为明确分析任务 `Explain the task model` 后验收其完成语义，未放宽产品完成条件。

本子任务修改路径：

```
chaos_agent/acp_adapter.py
chaos_agent/acp_task_service.py
chaos_agent/application_model.py
chaos_agent/foreground_tasks.py
src/code_agent/interfaces/task_controller.py
src/code_agent/interfaces/task_service.py
src/code_agent/interfaces/task_stream.py
src/code_agent/acp/adapter.py
src/code_agent/acp/event_bridge.py
src/code_agent/acp/AGENTS.md
tests/test_acp_integration.py
tests/test_acp_task_service.py
```

Host/Interfaces 契约建议由根集成：Application.tasks 共用 foreground；TaskService 仅薄公共界面；外部 token 取消清理后持久结果/owner；taskless历史执行要求已知来源；ACP checkpoint 投影不取代 Task 权威。旧 factory/test迁移、context显式接线、CLI本地读取由其他 S8 子任务负责。
