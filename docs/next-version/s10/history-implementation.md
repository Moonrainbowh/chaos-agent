# S10 Sessions/Core 历史主线实施物证

上述回归的最终模块复测：Root 将隔离 scripted-model fixture 显式设为 `gpt-4.1` 并 assert 已知 tokenizer 后，全部 HostProgressIntegration 8 PASS，68.153s（history-lease-integration-final.log）。原两项错误均通过，生产 capacity/lease/硬预算未提高；历史修改仍仅 journal 缓存identity兼容及其反例/契约。

全量发现 Host progress 租约回归：原2个长轨迹 integration 稳定复现；隔离 seam 原异常为 `Host progress frozen identity changed`、candidate_limit=30/hard_tool_limit=128（history-lease-original-exception.log）。7条消息的2→3候选上限最小反例先红后绿。journal 现按 observer identity 校验缓存；参数变化一次有界 replay，保存CAS以原 durable cursor为expected，不修改lease/硬额度/pure observer。反向3→2与目标改变也按新参数重投影。最终2项focused PASS（含并发同序号修改热缓存检查），history-lease-focused-final.log。原两项重跑已越过progress但命中独立 `RequestCapacityError`（history-lease-repaired-tests.log）；Root负责计量fixture范围，未把该结果称为integration PASS。

成本 consumer 补齐：公开 `conversation_usage_summary(..., conversation=False)`，同 SQL Unit 增加 thread-only scope；两条 sibling 各自与原 accumulator 对照通过，1项 focused PASS，history-usage-scope-tests.log。默认 conversation 行为保留；未创建第二金额账本。

独立监督补修：usage.tool_call 字符串 `"{}"` 原 SQL 被误认成空对象；已改为 json_type 与值共同判定。仅复跑3个相关用量测试全部 PASS，新增12种 tool_call 值与原 UsageAccumulator 对照（truthy字符串、实际空object/array、false、零、空字符串、null）。日志 history-usage-repaired-tests.log；未重复完整套件，待 Root 重验监督矩阵。最新产品哈希已重生成于 history-files.json。

主线已实现并提交给 Root 集成与独立监督；本文件不宣称 S10 全量验收通过。S9 candidate 冻结，未改用户 authentication、真实库、Provider/Host 或 S11。显式产品修改范围及 SHA256 见 history-files.json。

## 实现与边界

- v25 history companion 保存稳定旧 UUID、tool call/result 索引、display/Unicode casefold 映射、原事实 revision 与 Host progress cursor。旧日志保持原始事实；同序号 UPDATE/DELETE 使缓存与恢复 CAS 失效。恢复版本覆盖 owner/状态/执行/消息/事件/workspace mutation，普通预算 admission 不加入版本材料。
- Core 初次以 100 行/1MiB 页重放旧历史，随后从持久 cursor 增量；热返回重查 epoch/revision，进展集合保留 S9 bounded 判定且不淘汰已接受项重复计数。pending SQL 单独覆盖整个 journal，不能由 context 尾页判断。旧重复 ID 保守；任何 role 的 tool_call ID 都参与原全局重复保护。
- Context 保留最新 user 和完整 assistant/tool group；行/字节不足时明确失败，不拆组。旧 UUID 冷回填仅取 metadata；旧 display 冷回填逐条有界解码一次，后续 list/read/search 在 SQL 取片段。
- context/window/note/checkpoint 元数据页与 event/result/goals/checkpoint 查询均先 SQL 字节检查，再取正文。result 保留最新 RUNNING 与最新同 generation/NULL-safe subject 结果各自选择；旧 ACP marker 按 exact label 先过滤再 limit。
- Conversation Provider 用量使用 SQL request 分组最后有效 Usage，不物化消息树/全部事件。Python 只接收汇总、最新 Usage 和有界 models；不把 S7 预算视为 Provider usage。

## 实际验证

- 最新针对性测试：22 PASS，history-counterexample-final-tests.log。包括 None subject、多 RUNNING/result 噪音、跨页 pending、旧 ID、非 assistant call 元数据、完整工具组、Unicode ß 偏移、巨大 arguments/coverage/event/projection、同序号修改 CAS 与热缓存并发修改、语义 checkpoint 缺字段不被 overlap filter 隐藏、跨 conversation UsageAccumulator 等价及巨大 Usage 先 SQL 拒绝。
- 前一批完整 Core：202 PASS；Sessions：247 PASS、2 skip（当时含15项新反例）。后续接口和额外反例由上述22项验证；最终全量、S5-S9守门与监督由 Root 进行，不能把前一批结果写成最终全量。
- 部分初跑日志保留：history-first-tests.log 为 SQLite UPSERT/trigger 冲突修复前；history-counterexample-tests.log 含 Windows 测试连接清理问题；history-after-initial-failed.log 为 benchmark 在追加增量后错误比较旧 UUID 与新 latest 的断言，已改为按原序号重新读取。

## 可复现隔离基准

锁定 candidate .venv CPython 3.13.2，只用 main/src + main；history_baseline_benchmark.py 内部显式 TemporaryDirectory 并在 Repository 构造前 assert 路径。原 fixture 每组 user/assistant/tool，tool result 4096字符，600/6000消息及等量事件；before/after 相同 fixture，增量两条消息放在其他测量之后。2次运行取中位时间与最大 tracemalloc Python peak；不包含 SQLite native RSS、网络或整轮 Host 延时。查询数为连接 trace 的 SELECT 次数；decode 数为真实 codecs 调用，不能冒充 SQL 扫描行数。

运行命令（从 main root，PYTHONPATH=main/src;main）：`candidate/.venv/Scripts/python.exe docs/next-version/s10/history_baseline_benchmark.py docs/next-version/s10/history-after.json --after`。baseline 已冻结，不覆盖；JSON 是精确证据，表内数值仅格式化。

### before

|消息行|case|解码 message/event|SELECT|中位 ms|Python peak MiB|
|---:|---|---:|---:|---:|---:|
|600|load_messages_all|600/0|3|23.22|2.011|
|600|load_events_all|0/600|3|19.47|0.456|
|600|load_message_records_all|600/0|3|28.47|2.157|
|600|load_message_records_latest20_existing|20/0|3|4.58|0.097|
|600|persistent_history_list10|600/0|6|54.97|2.166|
|600|persistent_history_read_last|600/0|6|42.95|2.170|
|600|persistent_history_search10|600/0|6|46.99|2.170|
|600|host_progress_load_replay_pending_revision|600/0|3|54.15|2.008|
|600|actual_core_host_progress_facts|600/0|8|59.25|2.014|
|600|recovery_checklist_actual|1200/600|28|103.78|5.846|
|600|count_max_sql_candidate_not_product|0/0|2|3.52|0.021|
|6000|load_messages_all|6000/0|3|205.61|19.904|
|6000|load_events_all|0/6000|3|168.02|4.408|
|6000|load_message_records_all|6000/0|3|259.17|21.440|
|6000|load_message_records_latest20_existing|20/0|3|5.74|0.097|
|6000|persistent_history_list10|6000/0|6|538.17|21.455|
|6000|persistent_history_read_last|6000/0|6|412.60|21.455|
|6000|persistent_history_search10|6000/0|6|464.22|21.455|
|6000|host_progress_load_replay_pending_revision|6000/0|3|559.96|19.904|
|6000|actual_core_host_progress_facts|6000/0|8|568.83|19.910|
|6000|recovery_checklist_actual|12000/6000|28|860.56|58.080|
|6000|count_max_sql_candidate_not_product|0/0|2|4.11|0.021|

### after

|消息行|case|解码 message/event|SELECT|中位 ms|Python peak MiB|
|---:|---|---:|---:|---:|---:|
|600|core_progress_cold_paged_replay|600/0|42|123.59|0.652|
|600|core_progress_hot_no_new_messages|0/0|19|17.47|0.086|
|600|legacy_display_cold_paged_backfill_search10|600/0|619|786.45|0.102|
|600|legacy_uuid_cold_metadata_backfill_read100chars|0/0|7|35.11|0.093|
|600|history_list10_hot|0/0|43|38.07|0.041|
|600|history_read_last_100chars_hot|0/0|7|7.31|0.031|
|600|history_search10_hot|0/0|43|38.45|0.044|
|600|context_messages_complete_tail|202/0|15|16.21|0.613|
|600|recovery_checklist_hot|0/0|38|19.77|0.054|
|600|read_history_page_latest20|20/0|4|4.96|0.105|
|600|core_progress_hot_two_new_messages|2/0|23|37.16|0.098|
|6000|core_progress_cold_paged_replay|6000/0|258|1195.05|0.739|
|6000|core_progress_hot_no_new_messages|0/0|19|26.08|0.085|
|6000|legacy_display_cold_paged_backfill_search10|6000/0|6073|8096.66|0.184|
|6000|legacy_uuid_cold_metadata_backfill_read100chars|0/0|12|161.62|0.283|
|6000|history_list10_hot|0/0|43|43.23|0.039|
|6000|history_read_last_100chars_hot|0/0|7|7.65|0.028|
|6000|history_search10_hot|0/0|43|42.84|0.043|
|6000|context_messages_complete_tail|202/0|15|17.72|0.613|
|6000|recovery_checklist_hot|0/0|38|26.95|0.047|
|6000|read_history_page_latest20|20/0|4|5.54|0.105|
|6000|core_progress_hot_two_new_messages|2/0|23|39.55|0.098|

## 已知限制与验收反例

冷 replay 仍需完整扫描旧日志，但峰值页有界；6000行冷 replay 比原一次读取慢，不能声称延迟普遍改善。旧 display 冷迁移有明显一次性时间成本；查询缓存初始化后正文解码数量为零。SQLite SQL 搜索、用量聚合仍会扫描事实，并非所有查询 O(1)。新增 conversation 用量 API 由22项测试验证，未包含在既有 before/after timing 表。

任意巨大 required group、单 legacy 消息超过16MiB、pending arguments 合计超过1MiB、progress projection 超16MiB、notes coverage/metadata 超预算、models/Usage 超限定或 token 精确整数溢出均明确失败；不静默裁剪、猜测用量或绕恢复所有权。旧导出 all-load API 保留兼容；默认 production consumers 必须由 Root 验证接到新 API。

验收应继续检查：重启后仅增量2条读取；same-sequence payload 修改拒旧恢复版本/热缓存；pending 早于尾页仍闸门；旧重复 ID 不被任意 later tool result 解除；相同前缀巨大或 corrupt source 明确失败；obsolete checkpoint 元数据不能因总数128误拒仍必要的最新候选；None subject 的 accepted_partial/cancelled 不丢失。
