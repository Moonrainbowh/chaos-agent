# S5 未决动作核对

普通 `resume/events/resume_thread` 和 Core 直接入口都从持久消息判断未决动作，不依赖状态名。连续点击、数据库重新打开或进程重启不会清除它。原 ID/name 与消息顺序必须配对；历史重复 ID 保守阻断，新模型调用复用旧 ID 在写入历史前拒绝。

操作者入口：

```text
chaos-agent task recovery <task-id>
chaos-agent task resolve <task-id> <call-id> <message-seq> <recovery-version> <decision> <reason> <evidence>
```

先查询 `pending_action_records` 与 `recovery_version`，然后明确提交一项决定；理由与证据是独立参数，含空格时用终端引号。版本发生任何变化后重新查询，不自动改用新版本重试旧决定。

- `durable_receipt`：只读动作已有可信引擎回执，补齐丢失的工具反馈。核验同线程唯一原 assistant 事件、其后的匹配 request 与完整 result；Shell 来源、调用前或冲突回执拒绝。紧凑名由 Host 解析，未知插件/MCP 不冒认只读。
- `local_mutation`：本地写入已有唯一 COMPLETED mutation，Host 在原 mutation gate 内核 task/call/owner、原操作、物理目录 fingerprint 与当前文件字节 SHA。PREPARED/GAP、文件变化或同路径目录替换均拒绝。匹配只证明此回执与当前事实，不证明任意外部工具恰好一次。
- `operator_executed/operator_not_executed`：操作者明确报告结果，并给出理由与证据。持久工具反馈标注人工来源、`is_error=true/verified=false`；不伪造工具成功或验证证据。核对不会执行或重放原动作；需要新的读取或其他动作时仍由后续新请求经过普通权限链。

每次决定绑定 task/thread/call/原消息 sequence 和完整持久版本，在同一事务重新检查并追加反馈与解决事件。过期、重复、错任务、活跃任务和错工作区零写入。核对与普通执行分别授权，核对方法不注册为模型工具；API 的 `operator_authorized` 仅供可信操作者适配器传入，模型正文不能赋予此权限。CLI 的显式 resolve 参数是操作者操作。

PAUSED/INTERRUPTED/WAITING_DECISION 也不能单独证明动作已经退出。执行 owner 纳入版本；事务内仍存活或无法确认已死的 owner 阻断核对。Foreground 的 finally 按本次 instance CAS 释放，不删除其他 instance。Host 用 PID 与创建时间确认死 owner，核对事务才清理该旧 owner，保留任务状态与未决历史。登记/接管的旧覆盖行为及父子归属仍属于 S7，S5 不宣称已修完它们。

本地变更核对同步可信路径，并推进 code_generation、清除旧 subject_hash/verified_facts；人工报告也保守失效旧验证状态。旧 ledger 证据保留为历史，新 generation 不匹配旧证据。下一次准备由现有验证服务取得新 subject；核对本身不产生验证通过。预算、任务契约、工作区归属和原历史不重置、不删除。

当前 CLI 核对仍经过既有应用装配；Provider/Shell 不就绪时的纯历史入口解耦属于 S8。手机核对响应适配属于 S12，此处不宣称真机完成。
