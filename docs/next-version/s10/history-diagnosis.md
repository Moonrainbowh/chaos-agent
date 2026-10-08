# S10 History 只读诊断与隔离基线

本报告只诊断 S10；没有修改产品源码、candidate、用户 authentication、默认数据库或真实 Host。需求依据为下载规划 S10、当前 `docs/next-version/s10/requirements.md`、根 Python 扩展及 Sessions/Core/ContextWindows 契约。S11 项目记忆不实施。

## 结论与推荐

保留当前未显式配置时的 production semantic 路径；现有证据不能证明 persistent/summary/boundary 对真实模型任务更好。四条路线都存在历史读取问题，换默认不能解决它。应先提供 Sessions 有界查询，再把默认执行、恢复、结果投影及 History 工具切换过去。

S9 的 accepted progress sets 有数量上限，但每次构建仍完整读取/解码日志；在多个准入点重新重放。因此只把全量 `load_messages` 换成每页读取、每次仍扫完所有页，只能减峰值，不能满足“不随整段历史反复全量加载”。进展需要按持久序号增量维护，重启与旧库可一次有界分页回放，而不是每个动作重新扫描。

## 当前实际路径

| 路径 | 实际行为与代码位置 |
|---|---|
| 默认 semantic | `chaos_agent/application_context.py:141` 的 `profile.context_policy is None` 分支 → `chaos_agent/context_runtime.py` → `ThreadAwareContextBuilder`。`src/code_agent/thread_intelligence/context_builder.py:94` 每次全量 `load_message_records`，之后才应用旧 checkpoint、选消息、压缩；`_apply_checkpoints`、`_checkpoint_is_valid` 再构造范围集合/遍历原消息做摘要来源核验。 |
| persistent | profile 有策略时 Host 调用 `build_managed_context`；`src/code_agent/context_windows/persistent_builder.py:38` 全量读取、找 latest user，再遍历所有旧窗口核验来源，最后选 active。保留原消息，非摘要替代。 |
| summary/boundary | 同一 Host managed 入口的 `WindowContextBuilder`；`src/code_agent/context_windows/builder.py:18` 全量读取、核验旧窗口、选择 active。HandoffWriter 通过共享 BudgetedWindowClient。 |
| metadata 自动 opt-in | `src/code_agent/config/context_policy.py:6`：未显式 `context_policy` 且 `model_metadata.token_budget.enabled=True` 时返回 persistent；缺省 enabled=False 则 None。显式 policy/False 优先。不能把 metadata-enabled 用户冻结策略静默改为 semantic。 |
| Core 恢复开始 | `src/code_agent/core/_engine_run.py:148` 和 `:216` 全量读取，`pending_calls` 扫描全历史，同时保存所有 used call IDs。 |
| Core 每轮上下文 | `_engine_run.py:239` 先全量读取；默认 semantic builder 随即独立再次全量读取 records，前一次请求中的全部消息对该 builder 没有消除第二次读取。 |
| Core S9 progress | `_engine_run.py:178` 全量 load 后 `observe_host_progress`/`interaction_revision` 重放；`_engine_turn.py:39` 模型准入、`:353` 工具批次准入、`_engine_convergence.py:45` 收敛、`engine_actions.py:279` verifier 准入再次调用。开始和 prepare 也调用。这里统计的是源码调用点，不冒报端到端每轮固定次数。 |
| durable recovery | `src/code_agent/sessions/_recovery.py:18-19` 完整 messages/events，然后 `_action_recovery.py:25` 的 recovery_snapshot 在同事务再次 SELECT messages/events 全量，解码消息并把所有 payload 再序列化计算 CAS version。 |
| durable result | `src/code_agent/interfaces/task_controller.py:391` 全量 events，再逆扫找 latest running instance/current-generation TaskResult。 |
| UI/CLI history | `src/code_agent/interfaces/history.py:35` gather 加载全部 messages/events/goals/checkpoints；新 CLI readonly query 仍依赖该投影。成本视图 `interfaces/cost_control.py:56` 还加载 conversation events 与 task events。 |
| persistent history tools | `src/code_agent/context_windows/persistent_history.py:49` 无论 windows/list/read/search 先完整 load records；`list_or_search` 对全部记录 JSON 序列化后才 page。`read_item` 找最后一个 ID 也需要完整解码。 |
| compatible window history | `src/code_agent/context_windows/tools.py:70` load all 然后筛选 start/search；最终最多8条 search 或单条16k片段不是构建有界。 |
| context bookkeeping | `src/code_agent/sessions/_context_journal.py:9` `_rows` 无 LIMIT；context_records window/request/usage 和 append CAS 均读取完整该类记录。`WindowContextBuilder._measurements` 完整 usage 再 sum。预算侧必须复用 S7 权威 owner 语义，不另建扣费计数。 |

## 已有身份、分页与配对保证

- `messages.sequence`、`events.sequence` 是各表全局 AUTOINCREMENT，并不是每 thread 连续的 1..N；现有按 thread 排序是稳定的，其他 thread 的插入造成合法空洞。不能用 `MAX(sequence)` 代替 count，不能以 `sequence + 1` 假定下一条一定存在。
- `load_message_records(thread, before_sequence=?, limit=?)` 已在 SQL 中 DESC LIMIT 然后 Python reverse；exclusive cursor，返回升序。没有 `after_sequence`、闭区间、role、by-sequence、search、bytes cap；limit 只有正数，没有最大值。默认 None 保留全量兼容。
- persistent `item_id` 为 UUIDv5(`chaos:item:{thread}:{sequence}`)，window 首 ID 同样 UUIDv5；这两者不是 conversation canonical node。`conversation_message_refs` 保留 fork 复制的 canonical node，而 fork 后消息序号不同、thread-local item ID 也不同。保留旧 UUID 来源查找，拒跨 task/thread 查询。
- messages 保存完整 Message JSON、附件引用、ToolCall arguments、结果以及原时间；原日志不被摘要覆盖。`pending_calls` 要求全历史 call ID 总出现次数为1，且工具结果在调用之后、ID与原 name 同时匹配；重复 ID 即使某个后来的结果匹配也保守 unresolved。
- window `select_window` 校验 source range digest，`closed_group_ends` 只在完整 assistant/tool 组之后切窗。查询截取最新20条可能刚好从 tool result 开始，直接把这个页当 Provider 上下文或恢复判断会丢调用/绕过门。
- 附带现存正确性问题：`_recovery.py` 从 `load_events` 返回的 AgentEvent 对象 `getattr(sequence, 0)` 求最新 event，但 AgentEvent 不携带持久 sequence。因此 latest_event 恒为0，notes_lagging 不能识别单纯事件落后。新增高水位聚合必须读 SQL event sequence，不延续该投影错误。

## 实测基线

运行：candidate 锁定 `.venv/Scripts/python.exe`（Python 3.13.2），`PYTHONPATH` 明确 main/src + main，执行 `docs/next-version/s10/history_baseline_benchmark.py docs/next-version/s10/history-baseline.json`。脚本先 assert database_path 位于本次 TemporaryDirectory，再构造 Repository；重新打开同库前再次检查。没有 Provider、网络或默认 session 路径。结果为 repository/真实组件的隔离读取基准，不是整轮 Host 延时。

600/6000 消息及同数事件；每组 user + assistant(read_file) + tool，tool 内容4KiB，调用 ID 各不相同。bulk SQL 仅填充有效原始 journal fixture，未建节点的旧消息由真实仓储兼容读取；所有计时路径使用真实产品方法。每项2次，时间中位数、Python tracemalloc峰值最大数；decode 数量 patch 真实解码函数，SELECT 数量由临时连接 trace 计量，包括 `_require_thread` 等查询。计时包括 tracemalloc，不能和无 instrumentation 生产性能等同。SQL COUNT/MAX 行是建议的隔离对照，不是已实施产品改进。

| 实际读取路径 | 600 rows ms / MiB | 6000 rows ms / MiB | 6000 rows 解码消息/事件 | SELECT |
|---|---:|---:|---:|---:|
| load_messages all |23.22 / 2.011|205.61 / 19.904|6000 / 0|3|
| load_events all |19.47 / 0.456|168.02 / 4.408|0 / 6000|3|
| load_message_records all |28.47 / 2.157|259.17 / 21.440|6000 / 0|3|
| existing latest20 |4.58 / 0.097|5.74 / 0.097|20 / 0|3|
| persistent list10 |54.97 / 2.166|538.17 / 21.455|6000 / 0|6|
| persistent read last 100 chars |42.95 / 2.170|412.60 / 21.455|6000 / 0|6|
| persistent search10 |46.99 / 2.170|464.22 / 21.455|6000 / 0|6|
| actual Core `_host_progress_facts` |59.25 / 2.014|568.83 / 19.910|6000 / 0|8|
| actual recovery_checklist |103.78 / 5.846|860.56 / 58.080|12000 / 6000|28|
| count/max SQL candidate |3.52 / 0.021|4.11 / 0.021|0 / 0|2|

recovery 的 SQL messages 和 events payload 实际各读取两遍，事件第二遍作为 SQLite rows 进入版本 material，没有事件 decode，故 decode 计数不能替代 SQL 读取行数。standalone replay/pending/revision 的原始数据也保存在 JSON，不需要用它冒充 whole Core。两个规模均在 reopen 后验证已有 UUID item identity 不变。

产物：`history_baseline_benchmark.py`、`history-baseline.json`、`history-baseline.log`。JSON 记录被测4个关键源码 SHA256；后续对比必须使用同数据/运行环境，并说明代码变更，不能预填改善比例。

## 最小实现候选及契约

1. Sessions 增加明确有界 `after_sequence/before_sequence` 页、单条按序号查询、counts/high-water/latest role、latest current-task result/event 查询。页限制 row 与 payload bytes：单个大消息用现有 max_chars 片段读取语义，不能用 row cap 冒称字节有界。统计 COUNT/MAX 在 DB 内聚合；latest events 必须限制 kind/task/generation/subject/timestamp 而不是只 LIMIT 最后1条任意 event。
2. 原 UUID lookup 若只对每条历史重新计算再比较仍是 O(N) scan；采用可迁移的 companion stable-ID 索引或受 current thread 验证的序号定位映射。保留旧 UUID、不改变原日志。Unicode literal 内容检索必须与旧 display JSON 的匹配/offset 定义一致：直接对 encode_message compact JSON `instr` 不一定匹配旧 history 的空格/序列化形态；casefold 搜索不能以 SQLite ASCII lower 假装等价。可材料化兼容 searchable display text/fragment，或有界页扫描并返回 continuation/progress cursor；不得在 Python 全量收集命中。
3. 持久 pending/call-uniqueness 投影：append 在同事务登记 call occurrence 和 result ordering/name；duplicate ID 始终保守。旧库一次页式构建，继续执行前确认投影已覆盖最高序号；不能只检最新页或先 dispatch 后补迁移。以 task/thread/call/original sequence 维持恢复 CAS，聚合 high-water 不能丢原 CAS 的任意日志事实漂移拒绝能力。
4. Core 的 trusted HostProgress 增量 reducer/游标：一次有界旧历史 replay，之后只摄取 cursor 后新 paired facts；饱和 accepted sets 不淘汰/重收、用户输入去重 revision、原 Host resolver 身份、旧失败解决均保持 S9 语义。状态可与 task budget 单事务保存或提供 Sessions projection，避免第二套 supervisor。每轮 bounded tail 给 Context；具体 semantic builder 依据有效最新 checkpoint + uncovered suffix 查询原文，而不是 Core 全量读后 builder 再全量读。
5. 来源核验在首次 checkpoint 恢复或首次消费该不可变范围时做流式增量 digest/anchored state，后续复用同高水位已核对事实；无日志改写 API 的 append-only 保证仍要与 fork/copy/rewind、旧库缺索引、损坏数据拒绝联动。不能随意省略 source_digest 或所有 checkpoint 校验来换性能。
6. 恢复/结果/UI projection 消费相同有界 reader；保持 legacy all-load API 供显式导出/测试兼容，不让 production key path 静默退回全量。context windows/request latest 元数据与 usage 聚合也应只取必要记录；不改变 S7 实际 usage/unknown liability/cancel/owner 账本。

建议主要实现范围：Sessions 新查询/投影 mixin及迁移；Core SessionJournal + Run/Turn/Convergence/HostProgress；ThreadIntelligence semantic builder；ContextWindows builders/history tools；Interfaces result/history；Host readonly history接线。原 auth 和 S11 memory 边界不参与。

## 必须验收的反例

- 600/6000/更长历史，同样最新页/单条查询只解码有界行；append 后下一轮只重放增量事实。禁用 all-load 方法或记录 SELECT范围，不能仅比较输出长度。
- 页边界落在 assistant 两个 calls 与两个 results 中间；跨页配对完整、尾端未闭合必须拒 provider/tool；重复旧 call ID、原名称不匹配、结果在调用之前均不因裁剪放行。
- 大于页字节额度的单条消息/结果：片段可追溯；required user/完整调用组不能静默丢弃，必要上下文无法放入明确请求前失败。
- 跨 thread 全局 sequence 有空洞、fork canonical IDs共享而任务 IDs各异；旧 item UUID仍找原文，未知/他任务 ID拒绝，升降游标不重复/遗漏。
- latest result 后追加大量 MODEL_EVENT/其他task facts仍找到当前权威 result；新 run、generation、subject、updated_at 漂移拒旧结果/旧证明。
- 单纯追加事件导致 notes coverage 落后；count!=maxsequence；恢复版本对新消息/事件、状态、owner、mutation漂移仍CAS拒绝。
- 旧 semantic/summary/boundary/persistent 保存策略按原策略恢复；多窗、重启后 source anchors/digests 和工具 arguments/results原文不变，摘要仍辅助。
- literal Unicode/中文/emoji/带引号arguments检索、offset 分片连续；不能由 lower/casefold 长度变化使偏移跳字。
- S9 alias/timing/body/新失败无续租、related读取和真实失败解决仍可；重启不赠旧候选额度，持久 budget/hard ceilings和lease warning不回退。

诊断完成，等待主 Agent 的实施作用域授权；本报告不是 S10 实现完成或放行结论。
