# S10 Sessions/Core 历史主线接口

实施授权：先增量历史主线；不改 S9 candidate/auth/真实库/S11。以下接口用于其他 consumer 接线，旧 all-load 方法保留显式导出兼容。

用量 scope：`conversation_usage_summary(thread_id, *, conversation=True)` 默认按 conversation 汇总；`conversation=False` 仅 SQL 限定该 thread 的 events，不查/创建消息树，不改变同 Unit 的 request 分组及 Provider Usage 算法。用于 thread 自身 cache/incomplete 成本判断；权威 TaskBudget 金额账本不改变。

- `conversation_usage_summary(thread_id) -> dict`: UsageSummary 同名字段，`latest: Usage|None`、`models: frozenset[str]`；按 conversation heads 归属与全局 event.sequence 的 MODEL_STARTED request 分组，每组替换为最后有效 MODEL_EVENT Usage，首个 start 前有效 Usage 是独立 request。未知 request 标 incomplete；latest 是最后已知 Usage，不被未知末组清空。SQL 聚合，不加载 conversation tree/原正文，不含 budget charged/reserve。单 Usage metadata 上限4KiB、models ≤128且UTF-8总64KiB；超过明确失败。用量必须可在 SQLite signed64 整数中精确聚合，溢出明确失败而非浮点近似。

- `history_stats(thread_id) -> dict`: `message_count/event_count/message_sequence/event_sequence/message_revision/event_revision/message_epoch`。count 与 MAX 独立；epoch 只在既有消息 UPDATE/DELETE 时变化，append 只增 revision。revision 由 SQL trigger 维护，覆盖同序号 payload/时间/thread 变动。
- `read_history_page(thread_id, *, after_sequence=0, before_sequence=None, limit=100, max_bytes=1048576, role=None, newest=False) -> tuple[MessageRecord,...]`: after/before exclusive，全局序号可空洞；limit 1..1000/max_bytes 1..16MiB。单条超过额度明确失败，SQL先查长度再取payload；消费者不得静默丢 required group。newest=True 返回最新页的升序。
- `read_history_item(thread_id, *, sequence=None, item_id=None, max_bytes=1048576) -> MessageRecord | None`: stable old UUIDv5(thread,sequence) 保留；UUID companion lookup由 bounded metadata-only旧序号backfill，不解码整段正文。未知/跨thread ID返回None。
- `history_item_fragment(thread_id, item_id, *, offset=0,max_chars=4000) -> dict | None`: 原display JSON保留Unicode字符偏移；SQL取片段，不先读巨大正文。返回sequence/item_id/role/text/next_offset。
- `search_history_page(thread_id, query, *, after_sequence=0,before_sequence=None,role=None,limit=20,offset=0) -> dict`: literal原display JSON搜索，返回有界snippet/sequence/item_id/match_offset与next_sequence；current-thread only。
- `load_context_messages(thread_id, *, limit=100, max_bytes=1048576) -> tuple[Message,...]`: 最新完整调用组页，必要组过大明确失败；保留最新真实user（即使位于tail外）。pending gate独立SQL，不以tail决定动作是否完成。
- `pending_action_records(thread_id, *, limit=256) -> tuple[dict,...]`: SQL聚合全历史call occurrence/name/result顺序，重复ID保守pending；返回原sequence/name/id/arguments digest，超量失败，不只看尾页。
- `has_tool_call_id(thread_id, call_id) -> bool`: indexed/current-thread查询旧ID，Core不保留整个历史ID集合。
- `load_latest_task_events(task_id, generation, subject_hash, *, limit=32) -> tuple[AgentEvent,...]`: 当前generation/subject result及latest running事件，有界给TaskResult consumer；过滤后LIMIT，不能LIMIT任意最后32事件。
- `load_host_progress_projection(thread_id) -> dict | None` / `save_host_progress_projection(thread_id, payload, *, expected_cursor, expected_epoch) -> bool`: Sessions仅保存Core纯reducer的版本化state+cursor；CAS拒竞争/epoch漂移，不改budget。旧库首次有界分页重放，热路径只after cursor增量，模型正文/notes不成为proof。

恢复version以受保护message/event/mutation/state/owner revision + TaskRecord构造；预算admission不影响version（原version也未包含budget）。state/owner变化仍拒过期决定。pending receipts查询具体call事件，保留原recovery结果形式。

实施过程若签名有变化立即更新此处并通知主Agent，不把尚未实现接口说成已可用。

## 已实施补充与最终 DTO

- `read_event_page(thread_id,after_sequence=0,before_sequence=None,limit=100,max_bytes=1048576,newest=False)` 返回 tuple `{sequence,thread_id,event:AgentEvent,created_at}`，页内升序；单巨大事件预先拒绝。
- `list_goals(thread,limit=None,max_bytes=None)`、`list_checkpoints(thread,limit=None,max_bytes=None,label=None)`：双None保留legacy全量，显式bound取最近limit后按旧created_at/id升序；仅给bytes时implicit row cap1000，仅给limit时implicit bytes cap1MiB。label是exact SQLfilter，先filter后LIMIT；内部context标签仍隐藏。
- `history_stats` 包含 `message_first_sequence`（SQL MIN）；所有COUNT/MAX/MIN区分全局序号空洞。
- `context_record_page(thread,kind,limit=100,before_rowid=None,after_rowid=0,newest=False,max_bytes=1048576,offset=0)` 返回原dict+`_rowid`；ASC默认/newest DESC。`context_record_item(thread,kind,identifier,max_bytes=...)` 取单条；`context_record_for_sequence(thread,sequence,kind='window')` 取归属window，initial返回None。
- `context_window_page(thread,offset=0,limit=10)` 返回旧initial UUID窗口在内的 `{items:[{window_id,start,end}],next_offset}`；`context_window_bounds(thread,window_id)` 返回一个bounds或None。只读取元数据/序号，保留旧window/item ID。
- `semantic_checkpoint_page(thread,limit=100,offset=0,newest=False,max_bytes=1048576,excluded_ranges=())` 返回原SemanticCheckpoint对象，顺序同legacycreated_at/id；≤128闭区间SQL排除相交来源，必要候选继续可分页查，缺失range的损坏记录不能通过NULL过滤隐藏。
- `context_usage_totals(thread)` 返回 `{spent,reserved,task_limit,count}`，复用原shared owner/origin过滤与unknown预留语义，仅聚合、没有扣费逻辑。
- `context_note_page(thread,identifier=None,query=None,offset=0,limit=1,max_bytes=1048576)` 返回旧 `{items,next_offset}`；SQL unicode-casefold只在先核对≤128KiB note metadata后使用UDF，不先读取巨大文本。
- content-only兼容history检索支持`casefold=True`；折叠扩展RLE映射使返回offset指向原Unicode字符，literal/display检索保持原JSON序列化。
- `load_latest_task_events` 返回chronological AgentEvents：独立latestRUNNING1 + latest同generation/subject且含result object的LIMIT32，合并至多33条，不能相互挤掉。
- Progress读/写均以SQL长度16MiB为限，cached cursor不可超持久高水位；epoch漂移重放，热返回前复核revision/epoch。epoch覆盖原消息UPDATE/DELETE，同序号漂移会失效；append不重放旧前缀。

冷热边界：旧UUID序号索引metadata-only一次回填；旧display literal索引一次逐条≤16MiB回填，超大legacy正文明确失败。新正常append同事务保存display与item ID。原raw日志/canonical nodes不改写，旧树节点索引回填也按1000序号分页。
