# S12 中央审批与决定链路只读诊断

日期：2026-10-04。范围：当前源码与 S12 规划，未启动 Application、未构造 Repository、未读取设备文件或凭据、未操作现有 Host。本报告是需求阶段方案，不代表实现或验收。

## 结论

手机审批当前确实断链。HTTP Host 入口未启用 dispatcher 的交互审批，策略 ASK 会返回 `approval_required`，不会形成手机可回答的审批卡。即使单独启用 `interactive`，现有 ApprovalBroker 只有进程内 Queue/Future，远程没有读取或响应适配，也不满足持久身份、TTL、状态版本和重启核对要求。

待决定与待审批须区分。WAITING_DECISION 已有持久 Task、结果及 S5 未知动作核对服务，不能把所有等待都转成 approve=True，也不能用普通 continue 消息解除未知副作用闸门。最小改动应补中央审批的持久请求/响应契约，并让 Remote 投影既有 Foreground 决定能力，继续复用原策略与实际执行器。

## 已定位源码

以下路径均相对于 `F:/code-ai-chaos/chaos-16-agent`，符号以当前源码为准。

| 位置 | 事实与影响 |
| --- | --- |
| `chaos_agent/cli.py:191` 附近 Host 分支；`:198` 交互设置 | Host 在设置 `application.dispatcher.interactive = command.kind is TUI` 前返回 `serve_host`；不是 TUI 交互入口。 |
| `chaos_agent/action_dispatcher.py:123,196`，`task_dispatcher.py:57,149` | Root / TaskScoped dispatcher 默认 False；TaskScoped 装配复制该值。Root 先 preflight、可信 edit-plan、精确 process-rule，再 `authorize_action`，最后 `_run`。这些顺序不能改变。 |
| `chaos_agent/permission_dispatch.py:35` `authorize_action` | 原动作与 plugin 展开动作分别评估；任一 DENY 优先；ASK 且非交互返回 approval_required；交互用中央 broker 请求。当前调用未传 typed execution_context。 |
| `src/code_agent/interfaces/approval.py:78` `ApprovalRequest` | 含 request_id/name/arguments/risk/target/reason/edit_plan；没有 task/thread/owner/workspace/state_version/TTL。request_id 当前直接使用模型动作 ID。 |
| 同文件 `:112` `ApprovalBroker` | request 创建 Future、队列入卡、等待取消或 bool；resolve 只按 request_id 查 Future。重复点击返回 False，但无持久幂等回执；进程重启全部丢失。 |
| `src/code_agent/interfaces/tui_lifecycle.py:83` `listen_approvals` | `next_request` 是消费式队列，本地 UI 等待回答后继续取下一项。不能再让手机竞争消费同一队列；手机应走中央待决查询快照。 |
| `src/code_agent/interfaces/interaction.py` `InteractionBroker` | plugin confirm/input/select 也只有进程内队列/Future。它不是 Task 完成决定的持久事实，不能直接拿来充当 S12 持久授权。若 S12 扩到 plugin 交互，应仍是同一 Host 服务适配。 |
| `chaos_agent/remote/applications.py` `RemoteApplications.for_root` | 惰性组合备用项目 Application，共持久产品库；当前不配置审批能力。需要同时覆盖 primary 与备用应用，避免切项目后断链。 |
| `chaos_agent/remote/server.py:197` 路由 | 有 status/projects/sessions/messages/stop/events；无 pending approval/decision 查询或回答路由。 |
| `chaos_agent/remote/task_controller.py:55,87` start/_prepare | 单活动运行；非终态恢复原任务，终态续聊新任务。不能给审批回答另建 Task，也不能绕过单执行槽。 |
| 同文件 `:138,178` history/events | 消息快照和进程内运行事件游标；256 项 deque 回放没有明确事件缺口标记。Host 重启后没有 _RemoteRun，只能靠持久会话事实，不能依赖旧游标。 |
| `chaos_agent/remote/protocol.py:92` | TASK_DECISION_REQUIRED 目前只投影 waiting_decision 状态，丢弃决定原因/可用操作；手机无法识别正常继续、部分接受还是未知动作核对。 |
| `src/code_agent/interfaces/task_controller.py:65,90,248` | `resolve_pending_action`、`events` 的 owner/未知调用恢复闸门、`accept_partial` 是已有共享能力；部分接受仅 VERIFYING/WAITING_DECISION 且不声明验证通过。 |
| `src/code_agent/core/engine_completion.py` `_resolve_task_completion` | 无实际修改、验证缺证据等会进入 WAITING_DECISION；并非每个等待都是动作授权。 |
| `src/code_agent/sessions/_recovery.py:13,41` | recovery_checklist 给 task/thread/status/owner、recovery_version 和 pending_action_records；resolve_pending_action 要求 operator_authorized，写事务核对。 |
| `src/code_agent/sessions/_action_recovery.py:26,103` | recovery_version 哈希含 Task、触发器 history revision、TaskState、execution owner；事务拒陈旧版本、运行/终态、错 workspace、非唯一 call/message。核对不执行动作、不重放原 ID。 |
| `chaos_agent/pending_action_recovery.py:16` | Host 对本地写核对先拿 mutation gate，核真实 workspace fingerprint、原动作语义、回执及当前文件 hash；手机必须委托生产 Foreground Host 服务，不能直接调纯仓储冒充本地核验。 |
| `src/code_agent/sessions/_task_execution.py` begin/register/release/reconcile_stale | 原子 claim；精确 instance 才幂等，旧 owner 不可覆盖，release 按实例。owner PID + create_time 检测；未知探测保留。 |
| `chaos_agent/remote/pairing.py:38,43` | authenticate 每次读取原子设备摘要观察外部撤销；revoke 不逆转已执行动作。HTTP wrapper 只在调用前认证一次，敏感审批消费前还需重新校验设备身份并串行化消费与撤销边界。 |

已通读根 AGENTS.md、AGENTS.python.md、Host、Interfaces、Sessions、Core 与 Remote Feature 契约。中央 policy、单执行槽、未知副作用闸门、持久预算和不可伪造验证事实是本阶段继承约束。

## 最小实现建议

### 中央持久审批

在 Sessions 新增小型 approval repository mixin 与 schema migration，仍使用同一数据库；不保存明文设备凭据。持久行建议包含：独立随机 approval_id、原 action request_id、task_id、owner_thread_id、origin_thread_id、run_instance_id、canonical execution workspace、action digest、task/state 版本、created_at/expires_at、pending/approved/rejected/expired/invalidated/consumed 状态、响应身份摘要及幂等响应摘要。

独立 approval_id 避免不同任务使用相同模型 call_id 相互碰撞。动作摘要应覆盖原动作及可信展开目标/参数、edit-plan digest、执行 workspace 和当前执行身份；手机传回显示快照中的绑定字段，中央重新核对，不能信客户端重述的动作正文/风险。模型 arguments 仍是不可信数据，不成为权限声明。

`ApprovalBroker` 继续作为唯一交互入口，增加持久适配、只读 pending 快照与异步绑定响应；本地 TUI 的 request/next_request/resolve 兼容接口可保留，但所有最终响应共享同一消费门。不能仅把服务器端 Future 暴露出来，也不能新建远程 policy。传递 ActionExecutionContext 的变更限 `RootDispatcher.dispatch → authorize_action → ApprovalRequest/Broker`，TaskScoped 继续原作用域组合。

批准仅准许当前 live waiter 对同一已经 preflight 的动作继续一次，不是一条永久规则。审批持久批准与实际动作已执行之间没有分布式原子提交：崩溃后必须展示持久批准及动作日志/回执，恢复仍走 S5。没有 live owner/waiter 的旧卡可以核对/失效，不能仅因数据库 approved 就自动重新派发旧动作。

TTL 到期默认拒绝或等待，不自动批准。采用注入 UTC clock 便于确定性测试；Host 重启仍以持久 expires_at 校验，时间异常保守拒绝。消费事务核对 task/state/version/workspace/instance/action_digest 与状态；同一响应幂等键、同一决定重复返回先前回执，不能再次唤醒/执行；相反决定、错 task/version/digest、已消费/过期卡稳定拒绝。

状态版本应明确冻结的是审批安全相关事实（Task 生命周期、execution instance、TaskState generation/subject 与动作绑定），不要未经评估直接使用包含无关新事件的全量日志高水位，否则展示/遥测写入会使正常卡立即陈旧。S5 的 recovery_version 只用于 S5 核对，不因其名字相似而充审批版本。

### 最小手机决定适配

在 Remote 新增任务级 pending 查询与绑定响应路由，认证先于选任务/读卡/消费；从 session catalog 找明确任务与项目对应 Application，不允许客户端指定任意 workspace、runner、shell 命令或新授权对象。

等待决定快照来源是持久 Task/TaskResult/recovery_checklist：

- 无未知调用且原恢复条件可满足：显式继续，委托现有 Foreground 恢复，仍守预算/权限/冻结 runtime。
- 允许部分交付接受的任务：显式接受，委托 accept_partial，结果仍 unverified/partial。
- 未知动作：显示有界 request/call/message/version/workspace 与原事实，显式报告已执行/未执行或请求已有 Host 可核验回执；委托生产 Host 的 resolve_pending_action，持久理由/证据，绝不自动续跑原动作。核对后继续是独立显式操作。
- 原 runtime 不可用等其他原因：显示事实与可行下一步，不提供虚假通用“批准并继续”。

决定卡同样要随机请求 ID、TTL、绑定当前 Task/状态版本与幂等消费。现有 accept_partial 是先读后独立 transition，多入口竞争时不能仅在 HTTP 层查版本然后调用；需中央事务 CAS 或共享串行门再核当前版本。继续请求在原单执行槽成功注册前不能标成已执行，失败保持明确失败/可重试，不另建后台执行器。

### 重启、事件缺口与撤销

查询与刷新必须从持久 pending/Task/recovery 快照重建，事件是提示，不能是授权事实。事件 deque 漏掉游标时显式 reset/gap，让客户端请求原子快照；运行 ID/Host epoch 与 sequence 共同识别游标，避免重启的 0..N 被误认为旧运行已处理。历史快照包含当前待审批/待决定集合及游标，旧卡回答仍复核版本。

敏感回答消费前重新核 PairingStore 当前凭据身份；撤销与消费需明确线性化边界：撤销先完成则新回答拒绝；已消费批准不得追溯撤销已执行事实。最小运行策略可选择“已开始动作允许安全收尾，后续需要审批的动作维持等待且旧设备不能回答”，若还要求阻止自动允许的后续动作，则必须为设备撤销显式取消/暂停原任务并等原子清理，不能假设 WebSocket 关闭等于任务停止。

## 必需验证与未证明部分

实现后用显式 Temp 根/数据库/PairingStore/ProjectStore、构造前 assert 验证：真实 dispatcher ASK→中央 pending→远程批准/拒绝；原策略 DENY 不生成可批准卡；跨 task/request/workspace/version/digest 拒绝；两路并发相同响应仅消费一次；相反重复响应拒绝；TTL；取消；Host 重启无 waiter 的旧卡不执行；批准后崩溃保持 S5 未决；撤销后批准及消费竞争；待决定继续/接受/未知核对；事件缺口/刷新一致。

当前只有源码证据，本诊断未运行上述测试。HTTPS/WSS、真实代理/证书、真实手机、现有 live Host升级是独立验收/授权边界，不由本报告或离线 HTTP 测试证明。手机不可取得时只能标后端完成、真机 BLOCKED，S12 不得整体 PASS。
