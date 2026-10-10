# S9 独立监督终审

结论：**PASS**。首版因否定全仓范围仍驱动无关读取续租而 CHANGES_REQUESTED；修复后的冻结候选通过独立反例、守卫、实际规则/ContextAssembly 与标准全量。没有剩余 S9 产品阻断，可进入 S10。本结论不代替 S10 有界历史或 S12 真机验收。

监督未参与 S9 产品实现，未修改产品源码、提交或推送。独立读取计划 S9 卡、真实 implementation.patch、Core/Host/Verification/Interfaces 契约和具体代码后构造反例。审查脚本曾误打开默认用户数据库，不能声称本次审查全程隔离；经过、已知动作及未知影响见下文及 supervision-fixture-incident.md。

冻结对象：S8 base tree `252a1f4c6dc6e6a4f9cddbf032fd348fc9c943fe`；最终 31 路径 patch SHA256 `c5393147d107863aadaed2eae0464224b914cd1cf8452685ca9d53cd2fafddad`。候选目录 `C:/Users/Windows11/.codex/worktrees/next-version-s2-baseline/chaos-16-agent`，实际解释器为其 `.venv/Scripts/python.exe`，CPython 3.13.2。见 supervision-interpreter.log。

## 独立代码判断与反例

- `src/code_agent/core/host_progress.py:114` 从真实配对 assistant/tool、原 ID/name 与 canonical Host 身份重建事实。明确否定 scope/deep 子句经过有限词表过滤（同文件53、63行及 limits.py），被排除路径优先于父目录、broad 与搜索结果链。正文、失败、空换窗、新 tool 名不能成为正向进展；固定容量集合不淘汰后重接纳旧事实。首版反例 objective 为 `Do not inspect the whole repository; explain src/auth/ only.`，16 个无关文件读取得到两次续租；最终同反例不续租并提前停止。硬预算在首版也仍有限，原问题不是无限硬预算。
- `host_progress.py` 对 Host 已验证的 schema name/digest 与真实非空 notes/history 读取，仅给初始容量内候选，不续租、不证明完成。监督新增反例验证缺少 disclosed_tool metadata 的结果不成为候选；有效 disclosure+note 达到容量2后，revision churn 和20个新history内容不再改变候选或 renewal digest。
- `chaos_agent/foreground_tasks.py:148` 首次创建 TaskBudget 使用真实 select_budget_lease；`_engine_run.py` 的持久预算及 pending_calls 门未被旁路。独立真实 Application 反例把硬回合上限降至6，连续相关读仍受限且最后 execution_owner 释放。18项守卫覆盖 pending recovery、budget pause、child frozen scope，以及实际 Windows 子进程树 timeout/cancel 清理；树在 closer 结束前死亡、owner 在清理完成后释放、迟到写入为 false。
- `src/code_agent/verification/task_service.py:251` 的成功 facts 累计摘要用于进展；`task_assessment.py:13` 单独选择当前代际/subject、最终计划 verifier 身份及最新结果。监督新增 CRITICAL tests+build PASS 后记录同 build FAIL/UNAVAILABLE，历史成功 fingerprint 不改变，完成不再 VERIFIED；同代际实际文件字节变化亦使旧 proof 无效。既有真实 SQLite 负向 lease 测试再次通过，FAIL/UNAVAILABLE 不续租也不清停滞。普通 shell exit0、错误身份/范围、targeted milestone 不替代最终证明；critical 最终 tests+build 门保留。
- `src/code_agent/core/engine_completion.py:14` 将未产生变化的 MODIFY 留在 WAITING_DECISION。`src/code_agent/interfaces/task_controller.py:259` 与 foreground checkpoint 生命周期保存显式接受结果。监督新反例关闭临时 Application 后重开同一个 SQLiteSessionRepository，经公开 result 查询仍为 accepted_partial / unchanged / unverified，require_verified exit_code=5；模型解释不能自动 completed。显式 ENV0 的兼容结果为 unverified，已由继承测试覆盖。

修复后新增三条路径属于已有回归夹具：两个 repair 测试补 discoverable pyproject 声明并增加真实 ledger 最终 proof 断言，原完成、修复次数及命令数保留；compact10次写/改迁到公开 app.tasks.start/events 以取得真实 generation，原10结果/canonical身份/磁盘内容断言保留。persistent 测试源码未改，原8步轨迹由有限 schema/history 候选通过，未恢复按写工具名或正文计进展。未新增 loop/scheduler/framework，未将普通 parent allowflags=false 错认成全局 hard deny；child frozen ceiling 仍按原边界审查。

## 执行证据与复现

以下命令 cwd 为冻结候选，证据文件在主工作区 docs/next-version/s9。脚本显式以候选 root/src 作为 import 来源；实际 Application、SQLite、工具装配使用临时 fixture 和确定性 Provider。

```powershell
& .venv/Scripts/python.exe F:/code-ai-chaos/chaos-16-agent/docs/next-version/s9/supervision_probe.py C:/Users/Windows11/.codex/worktrees/next-version-s2-baseline/chaos-16-agent
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& .venv/Scripts/python.exe -m unittest code_agent.core.tests.test_budget_lease code_agent.core.tests.test_engine_budget_lease tests.test_managed_task_budget_pause tests.test_pending_action_recovery_integration tests.test_child_execution_scope tests.test_child_process_cleanup -v
```

最终 supervision-probe.log：52 PASS / 0 failures / 0 errors / 0 skipped，80.688s。其中45项继承现有测试，7项是监督新反例；不能称52项全新反例，也不能与标准全量相加作为不重叠总数。最终 supervision-guards-final.log：18 PASS，12.477s。重开反例另单独1 PASS，2.284s（supervision-reopen-single.log）。

复用 S8 独立真实加载脚本，以最终候选运行 supervision_rules.py 与 supervision_context.py，输出 supervision-rules-final.json 和 supervision-context-final.json。RuleLoader+WorkspaceBuilder 的 root/Core/Interfaces/Host/ACP 为1542/4175/5982/5917/2210 tokens，均在原6000规则预算内；总提示额度20000未增。真实 RuntimeContextFactory 通过显式 contextfactory seam 对 root/Core/Interfaces/Host 构建 ContextAssembly，完整规则文本都在 system prompt，guarded client 实际存在，不沿 `_inner` 猜装配链。

标准全量由主 Agent 在同冻结候选运行；监督独立读取最终 outer CHAOS_TEST_SUMMARY：30 suites，3218 discovered=run，30 skipped，0 failures/errors/expected failures/unexpected successes，unrun_suites=[]，exit_code=0；根 tests649/0、Core202/0、Verification75/0。见 all-tests.log、final-test-summary.json。30项 skip 保持为未执行项目，未冒称全运行通过。

监督独立运行冻结字节核验（supervision-freeze-check.log），并在主 cwd 使用候选 Python 复跑 `docs/next-version/s9/end_validation.py`。31路径 main/candidate rawbytes、patch SHA256 一致；原 authentication binarydiff SHA256 `7cbeb87002763fbe5de0bf33a4fd68193af6d66e5340be75f2a1edeb776befca` 未变；outer 成功解析，见 end-validation.json。

## 失败物证与审查脚本事故

首版 `211a0acd...` 28路径全量为30套件/3205项/30skip/4failure/0error；all-tests-initial-failure.log、snapshot-initial.json、implementation-initial.patch 保留。初次独立36项35PASS/1FAIL（上述否定全仓反例）保存在 supervision-probe-first-failure.log，不能沿用为新冻结 PASS。

最终候选第一次独立52项51PASS/1ERROR来自审查脚本提前绑定未patch的 `_session_path`，实际打开 `C:/Users/Windows11/AppData/Local/chaos-agent/sessions.sqlite3`。执行了标准 Repository 初始化及一次不存在临时 task.id 的 SELECT，未调用用户 task/message/event 写入方法；不能排除初始化元数据写入或 schema 迁移，因为无事前版本/hash物证。该主库 CreationTime 是2026-08-08，证明非此次新建。短连接 finally 关闭、脚本 close且进程退出；没有清理或回滚用户库。第二次52项51PASS/1ERROR为 wrapper 未公开 session_path 的 AttributeError，取路径前停止、未触库。两次日志分别保留在 supervision-probe-reopen-fixture-error.log、supervision-probe-wrapper-fixture-error.log。

最终路径取自 active fixture 范围内模块属性 `_session_path()`，在构造 Repository 前断言与临时 workspace 同源；单独重开反例及最终52整组均通过。详细边界见 supervision-fixture-incident.md。上述是监督 fixture 错误，不是产品修复，也不据此隐瞒事故或声称用户数据库内容未变。

未测范围：真实模型/API、外部设备、已上线远端/mobile真机、S10历史规模专项。当前有限读取相关性只是明确 scope/path/symbol 提示，不宣称通用自然语言理解；硬预算、最多三次续租及权限策略仍负责最终边界。
