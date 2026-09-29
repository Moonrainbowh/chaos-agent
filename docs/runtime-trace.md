# Runtime Trace

调试期间自动保存按行 JSON 格式的运行 trace。默认写入本地 `logs/runtime-trace.jsonl`，不需要每次手动设置环境变量。

PowerShell：

```powershell
$env:CHAOS_DEBUG_TRACE_PATH = "logs/runtime-trace.jsonl"
chaos-agent
```

如需暂时关闭：

```powershell
$env:CHAOS_DEBUG_TRACE = "0"
chaos-agent
```

每条记录包含时间戳、阶段、事件、耗时和有界安全元数据。当前覆盖：

- `turn`：模型回合开始；
- `queue.followup`：排队输入持久化前后；
- `context.build`、`context.prepare`、`context.semantic`、`context.finish`：上下文各阶段；
- `repo_index.refresh`：首次索引、增量刷新、文件数和 generation；
- `repo_index.warmup`：应用启动后的后台预热耗时、文件数和 generation；预热失败不会阻止后续按需构建；
- `model.stream`：模型流开始、完成或失败；
- `action.dispatch`：工具开始、完成、耗时和错误标记。
- `durable.message`、`durable.event`：持久消息/事件成功写入后的 thread、role/event kind、turn、字段集合、正文长度和短 digest；digest 只用于把同一条消息和 UI 投影关联起来，不是正文。
- `tui.thread`：新会话/恢复会话的开始、完成、失败、投影 epoch 和清理前后条目数。
- `tui.event`：TUI 收到并 apply 的 event，包含 source、thread、task、turn、投影 epoch、消息 role/digest，以及 apply 前后的 entry/transcript 计数；旧 epoch 的 event 会记录为 `dropped_stale`。
- `tui.render`：一次 flush 实际写入终端的条目数和字符数。

定位 durable message/event 与 TUI render 的关联时，按同一 `thread_id` 和短 `message_digest` 过滤，再按 `timestamp` 排序：

```powershell
Get-Content logs/runtime-trace.jsonl |
  ConvertFrom-Json |
  Where-Object { $_.stage -in @("durable.message", "durable.event", "tui.event", "tui.render", "tui.thread") } |
  Format-Table timestamp,stage,event,thread_id,projection_epoch,event_kind,message_digest,entries_before,entries_after
```

如果看到 `tui.event/dropped_stale`，说明旧 runner 的事件到达了 thread 切换之后，但已被丢弃；这正是防止旧回答混入新会话的保护路径。

日志不记录 Prompt、模型正文、源码正文、凭据或完整路径。任务卡住时，最后一条 `started` 记录就是尚未完成的边界；把同一 `thread_id` 的记录按 `timestamp` 排序即可定位等待区间。
