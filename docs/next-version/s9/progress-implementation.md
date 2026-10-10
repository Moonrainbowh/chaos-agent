# S9 Core 进展实现交接

实现已完成局部验证；不代表 S9 独立放行或候选全量通过。主工作区源码已停止修改，等待主 Agent 冻结与监督。

## 最终行为

- 单一纯 `host_progress` 函数重建持久 assistant/tool 的真实配对结果，使用 Host canonical identity。调用 ID、递归耗时/请求 ID、同输出参数变化、正文、opaque、错误及换窗不产生正向进展。
- 成功非空新读取构成有界候选，最大数量来自初始 soft tool quota；候选集合固定接受后不淘汰重收。候选只影响停滞守卫，不能续租。关联信息累计摘要供守卫与 snapshot 共用，不采用 latest A/B 摆动。
- 相关性来自冻结目标明确路径的段级前缀、明确全仓调查、明确带引号/下划线的 symbol 或明确 search/find query 对应的成功搜索真实路径链。未知自然语言关系保守作为初始租约内的候选，不做相似度推断。
- 同相关 read 签名实际失败后成功可成为解决失败；执行动作必须有 Host execution_attempted，原相同规范签名确有失败，随后实际 exit 0 才算解决失败。首次/普通 exit 0 不计验证或预算进展。
- 真实逻辑 generation/subject 直接进入 snapshot；可信验证仅通过可选 `progress_fingerprint(task,state)` 服务（无服务时为空），不从自由文本 verified_facts 或工具 metadata 声称 PASS。
- `failure_fingerprint` 保留诊断兼容，但从正向 digest 移除。distinct user content/attachments 才增加 interaction revision；恢复时重复原输入不自动作为新需求续租。
- 参数验证纠错优先计数，即使同回合出现新读取也不能抵消连续参数失败。正文仅决定是否需要一次无工具总结；不重置停滞。未验证/失败/无需修改的终态继续使用 completion/TaskResult 门。
- 硬预算优先、最多三次续租、持久计数及旧 budget 字段不变。新增公共 SessionJournal task 查询仅委托现有 SessionRepository 协议，未增表。

## 精确 S9 修改清单

本子任务最终修改以下 14 个源码/测试文件（相对主仓库）。`_engine_dispatch.py` 在本子任务中未修改；其真实配对持久化继续由既有生产路径完成。

```text
src/code_agent/core/host_progress.py
src/code_agent/core/_engine_run.py
src/code_agent/core/_engine_convergence.py
src/code_agent/core/_session_io.py
src/code_agent/core/exploration_repeat.py
src/code_agent/core/limits.py
src/code_agent/core/AGENTS.md
src/code_agent/core/tests/_engine_support.py
src/code_agent/core/tests/test_host_progress.py
src/code_agent/core/tests/test_exploration_repeat.py
src/code_agent/core/tests/test_engine_stagnation.py
src/code_agent/core/tests/test_repeated_read_reproduction.py
tests/test_host_progress_integration.py
tests/test_compact_supervision_integration.py
```

Core 契约仅同步进展条目；主 Agent 先前写入的 S9 boundary bullet 保留。其他 agent 的 completion/verification/Host 改动不在此清单。

## 诊断中发现并由主 Agent 修复的真实 Host 缺口

首次真实生产轨迹失败：Host `ForegroundTaskController._create_managed_task` 先建立不带 tier 的 budget，Sessions 将其视作旧兼容全硬额度且 final extension 已使用，Core 后续的 select tier 无法改变已冻结 budget。因此 16 个相关读不续租、24 个无关读也继续。

此问题没有通过测试直接重写 SQLite budget 掩盖；主 Agent 在 Host 首次建立 budget 时传 `select_budget_lease(contract)`。最终生产测试断言 Task 创建后的 `lease_final_extension=False`、soft<=hard，然后通过正式 events 执行。

## 最终验证

锁定 CPython 3.13.2，使用候选工作树 `.venv/Scripts/python.exe`，`PYTHONPATH` 指向主工作区 `src` 和根。候选源码尚未由本子任务同步。

```text
python scripts/run_test_suite.py --start-dir src/code_agent/core/tests
  core-final-313.log: 192 tests, 0 errors, 0 failures, exit 0 (4.092s)

python -m unittest tests.test_host_progress_integration -q
  progress-integration-final-313.log: 5 tests, 0 errors/failures, exit 0 (23.781s)
```

真实生产测试仅替换 Provider 模型流；Application / ContextAssembly / SQLite / Workspace / compact tools / Foreground / Core / 权限 / 默认 structured verification 均保留。没有关闭默认 structured verification：

1. 16 个相关不同文件，只输出工具调用，跨第5轮继续且真实续租。
2. 24 个无关新文件，每轮附“继续查看”，不续租且有界停止。
3. 同文件同内容反复读并附正文，配对结果完整且 paused。
4. 7 个不同 Markdown 文档真实写入，无正文；generation 至少7，不按形状收尾。Markdown 采用默认轻量相关验证，避免为本测试制造无关测试需求。
5. 失败读取和正文，不当新信息，不续租、有界停止。

纯事实测试覆盖 A/B 去重、参数/时间/ID 变化、路径段边界、搜索链、opaque/失败/换窗、集合饱和、失败解决、普通 exit0、重复恢复输入等15项。Core 总数还包括另一 agent 的 S9 completion 新测试，不能将192全部归为本子任务新增。

追加反例（`test_host_progress.py`，无新增路径）：真正 SQLite + Ledger 的受控 typed verifier receipts，经真实 Core snapshot 和公开预算 reserve。首次 PASS 消耗完标准12轮软额度，再同 subject/identity FAIL → UNAVAILABLE：digest 不变，两次都 LEASE_EXHAUSTED / 0续租，guard 连续计数到3，completion 两次均非 VERIFIED。`negative-lease-313.log` 的16项测试退出0（0.595s）。这里没有实际运行外部测试或 Provider，而是检验可信 verifier receipt 到 admission 的确定性语义；五项默认应用轨迹仍另行覆盖真实工具路径。

主 Agent发现并交由 Verification agent 修复了 PASS→FAIL 撤销导致 digest 变化的反例。`progress_fingerprint` 现在只表达同 subject 历史已经闭合的可信成功观察，负向撤销不产生新进展；最新 proof 独立处理 FAIL/UNAVAILABLE 撤销旧 PASS。历史进展摘要不能用于宣称当前验证有效。

历史失败日志保留：`core-first.log`、`core-second.log`、`progress-integration-first.log`、`progress-integration-second.log`。`progress-diagnosis-fixture.py` 是实现前旧 API 的只读复现脚本；其记录输出见 diagnosis 文档，不能直接把它当现实现的生产验收脚本。

最后限定文件 `git diff --check` 通过（仅换行提示）；非全量 CI、无推送、无合并、无 Host 升级。完整冻结、全套测试与独立监督由主 Agent 后续完成。

## 独立监督与全量发现后的修复

独立监督发现明确否定全仓文本仍触发 `_BROAD`，例如 `Do not inspect the whole repository; explain src/auth/ only.`。现通过有限中英否定词和明确子句过滤正向 scope，不做通用自然语言理解；`请勿/不得/不允许/别`、`do not/don't/never/avoid` 均有邻近测试。初始 deep lease 同样复用明确正向子句 helper，保留原 depth 词表和额度数值，否定“全仓库”不再先取得 DEEP30。

明确禁止读/调查的路径不作为种子，排除优先于父目录和 broad。返回含排除路径的 search/slices 保守不续租，不能借 symbol 搜索重建被排除路径；仅否定 edit 的句子不等于禁止读取。文件扩展名点号不拆成句子。否定 search symbol/query 不成为新路径链。

旧全量还发现两个实际入口问题，分别修复：

- Compact 10写/改保障测试之前直接运行 taskless engine，没有受 TaskVerification 维护的 generation；已将此唯一写轨迹迁到真实 `app.tasks.start/events`，保留原10动作/原正文结果/canonical身份/真实磁盘内容断言，不以成功工具名伪造进展。TXT 当前默认风险为轻量文档，未关闭 structured verification。其他观察测试路径不扩改。
- Persistent 原8动作恢复链含 HYBRID schema 加载回合、notes 与 history 真实读取；旧候选漏掉这些信息，会在历史读取前触停滞。现在复用已有 `disclosed_contract` 验证成功公开 name+schema digest，真实非空 history/notes 输出只作为初始容量内候选。相同schema/同notes正文仅revision变化去重，空换窗与notes_write不当进展，均不续租、不提供proof。`tests/test_persistent_runtime.py` 未改，原 raw.step=8 与原证据/窗口/usage 断言已通过。

修复后局部证据（均主工作区源码/锁定 Python 3.13.2，候选没有由本子任务改动）：

```text
negated-scope-complete-final-313.log: 36 tests PASS, 55.917s
production-regressions-313.log: 33 tests PASS, 28.741s
core-supervision-repair-final-313.log: 202 tests PASS, 4.183s
production-supervision-repair-final-313.log: 15 tests PASS, 78.089s
```

上述先前日志保留，对应最后统一 production 日志为 `production-supervision-repair-final-313.log`。完整新冻结及独立复审仍由主 Agent 完成，旧候选全量结果不等于这些修复的新候选验证。
