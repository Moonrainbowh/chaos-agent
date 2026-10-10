# P3 独立审查

日期：2026-10-07。对象：`98f9a744650890c69a305ae643443b384006123e`，基线 `afbd8f3ee7163cb71959a6af0bdfb77b6c082cdd`；区间只有此一个提交。结论：**PASS_P3_SPEC**。本规格轴未发现完成门语义缺失、越界或错误；整体阶段放行仍须处理Standards轴已提出的同步Guard I/O硬违规。P4 与 S16 总验收不在本结论内。

## 独立执行证据

使用本工作区 `.venv310/Scripts/python.exe -X utf8`，没有使用旧候选解释器、调用外部 Provider 或运行整仓全套。

| 独立组合 | 实际结果 | 日志 |
|---|---|---|
| S16公开链路、来源契约、budget lease、ChildResult、授权上限、partial cleanup | 50 tests，206.365s，OK，exit 0 | p3-independent-regression.log |
| Orchestration supervisor（包括超时、取消与用量保留） | 13 tests，2.819s，OK，exit 0 | p3-independent-supervisor.log |

首组命令为 `-m unittest tests.test_s16_source_completion tests.test_source_completion_contract code_agent.core.tests.test_budget_lease tests.test_child_result_contract tests.test_child_authorization_ceiling tests.test_runtime_partial_build_cleanup -v`；第二组为 `-m unittest code_agent.orchestration.tests.test_supervisor -q`。均在冻结工作区执行。代码 diff 为 `git diff afbd8f3...HEAD`，审查期间生产src/Host未变化；结束时其他Agent正在修改P4 harness准备文档/脚本，此报告不将其编辑中状态作为已冻结P4验收。

## 要求与物证

| 规格原句 | 实现与独立反例判断 |
|---|---|
| “来源路径须通过现有 workspace 路径约束，数量有界，启动后模型不能削减” | 可选 required_sources 全链路进入 ChildRunRequest，Host canonical guard、32条/每条1024字符，既有 child_budget checkpoint冻结。越界路径、旧binding、恢复显式空/漂移拒绝；省略参数仍取持久要求。 |
| “只认当前子线程的成功读取回执和完整文本结果” | Host通过实际restricted/root路由核built-in身份，完整历史成对匹配call/name/id，检查成功、text、total_lines、path及截断标志。独立测试排除plugin/MCP/metadata、slices、父读、重复、错误路径、读取失败、截断；空文件有效。未额外要求最新版或hash。 |
| “用已有调用参数和持久事件保存要求与读取事实” | 没有新表、子TaskRecord或第二权威库；读取事实重建自既有完整消息页，不用contexttail。热路径增量cursor与message_epoch失效。新SQLite连接恢复要求/纠正基线、tail之外旧回执与epoch改变均验证通过。 |
| “现有 runtime notice 告知具体缺项，继续同一子运行” | baseline与≤1000字符developer notices同事务落地；CAS纠正ID防旧边界、实际必要新读取才允许再次纠正。原子rollback与长路径无丢失通过。原v7原两轮行为加真实纠正回应后实际3child请求，准确failed/source_requirements_unmet和四项remaining；没有流耗尽假绿。 |
| “取消、硬预算或超时：保留原对应终态和未结算用量” | Core先检查取消及typed local/shared账本；不预留后回滚，不开新child或续预算。独立覆盖纠正取消、local round/tool及shared owner round/tool/token，另复验原supervisor超时/用量路径。恰好最后可用总结轮且来源齐仍合法完成。 |
| “来源读齐且已有最终答复”及“父侧 advisory 取最后一条非空、无工具调用的最终答复” | 空/空白答复准确failed/empty_summary，不再被后COMPLETED覆盖。仅来源任务选择最后最终文本，16384字符旧计划不挤掉新分析；无要求任务保原聚合和一轮完成。非空计划不作语义关键词裁判，仍须P4审查；verification未升级。 |
| “新任务以已有冻结 agent_topology=team 选择至少 STANDARD” | deep优先、team STANDARD、single/legacy analyze QUICK及小hard clipping均通过；已有QUICK预算不迁移。公开P2自然启动实际3父请求/2子请求，父非空最终答复已发送；无测试预塞预算或deep措辞绕过。 |

P1/P2累计规格包括真实prepared body角色归属、预算前注入、四Context策略重建、原prompt分类及父子职责分离；已审证据见p1/p2-independent-review。本轮公开回归再覆盖角色/intent/直接4read。P2过去PASS仅证明子输入/披露，不能回溯称旧父已完整交付；该缺口由上述当前公开断言闭合。

## 限制与报告准确性

结束时读到另一轴的 `final-standards-review.md`：认可其指出 `canonical_sources` 在async启动/snapshot直接执行真实Guard文件系统I/O，违反 `AGENTS.python.md` “阻塞文件I/O经asyncio.to_thread”。已通知Root，本轴测试不证明慢磁盘时事件循环取消响应；不得仅凭规格测试通过跳过这一修复。Root已存在 `p3-path-io-repair-plan.md`，新候选修复后的定点验证及最终放行待Root，不将方案当已实现。

`git diff --check afbd8f3...HEAD` 实际非零：原始日志有尾空格，新增测试有EOF空行。这是保留输出/排版问题，不是运行或规格阻塞；不将本次结果写成diff-check通过，也未编辑原物证。

此次未执行P4真实分析、Provider usage验收、五平台CI或整仓全套；Source gate通过不证明答案语义/引用正确或真实请求coverage。P4 harness是准备产物，相关实际放行仍须Root按冻结候选和真实记录核验。

本审查仅新增此报告和独立日志，未改生产或现有测试、提交、推送或启动服务。
