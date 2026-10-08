# Host 冻结父预算修复最终独立监督

结论：**PASS，仅四路径修复范围**。允许将 JSON 中精确 SHA 的 main 四路径同步到以463d为基线的候选；不是整体 S16、新CI或真实 child 验收 PASS。

独立实际比较候选 `463dc98099e3275a70daf896507b02d4ae387ae9` 与 main：两Host Unit修改只增加冻结父budget resolver及首次supervisor装配，Host AGENTS只增加该协作契约，新增11项邻近测试。换行归一仅用于阅读diff，raw SHA仍按实际文件字节记录。Host `AGENTS.md` 路径与作者 SHA 精确匹配，没有误绑定根 AGENTS。

默认BudgetLedger首次从持久实际TaskBudget取得token/tool硬上限；显式ParentBudget token/tool取min且其他约束保留。typed parent task/owner/origin不匹配、缺预算和非活动Task闭合。缺context的非法请求在缓存创建前拒绝，已消除默认200k污染；缓存后仍核owner。首次resolver await被同一锁包围，并发调用只能创建一个ledger。已有ledger跨release复用，但supervisor和取消token重新创建；runtime close清缓存。profile目录变化不重新扩大ledger。

实际原生产 compose_subagents + SQLite冻结父预算 + 局部事件engine执行，两版各20项最终回归全部exit0（Python3.10 8.742s、3.13 1.892s）。包含父1m/子300k到真实runner、父200k/token或tools3拒绝、显式ParentBudget只收紧、缺预算/错归属、首次并发和旧只读adhoc兼容。原EngineChildRunner.RUNNING/VERIFYING枚举适配明确成立；新增独立实际VERIFYING成功、PAUSED首次拒且不留ledger。

另两版各5项独立反例exit0：实际profile目录改变与release保同一ledger；显式tool4/children1约束跨release保持；真实Sessions owner预置pending800k后两次child reserve250k都拒、原pending/status/reserved不变且没有伪0结算；人为阻塞resolver await的两个并发首请求只调用一次resolver、一成功一预算拒；真实Task状态门。

进程ledger保已观察known usage250k；unknown的保守预留由持久Sessions层负责，不声称整个300k进程lease仍held。作者误导测试名称/注释已改为 `incomplete_usage_known_lower_bound_not_reset`，最终测试 SHA30649e...；独立最初相同误解导致断言300k失败，原日志 `frozen-budget-counterexamples310/313-initial.log` 保留，随后按真实knownusage250k修正并通过。没有改产品、费用或结果来补PASS。

最终raw SHA与实际命令/exit见 `frozen-parent-budget-final-supervision.json`。产品3路径 SHA在测试文字修正前后不变。当前候选仍463d，只能在同步后重新冻结来源、构建包与完整CI；真实父1m/子300k Provider usage、来源质量和终态尚待执行/独立审查。旧v1/v2失败与pending80703未知负债保留。监督者没有Provider请求、产品/candidate修改或发布操作。
