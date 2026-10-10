# P3 来源完成门实施报告

日期：2026-10-07。状态：IMPLEMENTED_PENDING_INDEPENDENT_REVIEW。本阶段使用当前 gpt-6.1-sol / medium，未调用 Provider、commit、push、启动服务或执行 P4。停止在 P3 独审门；最终整仓回归由 Root 承担。

## 结果与边界

生产 delegate schema → ChildRunRequest → 原 child factory → 原 AgentEngine → 原 ChildResult 已贯通可选 required_sources。最多32条、每条1024字符，Host 按冻结 workspace 校验相对路径，在既有 context:child_budget 保存 canonical 要求。缺省空向后兼容；旧 binding 缺字段等价空；恢复遗漏参数不关闭持久要求，显式空或冲突集合拒绝。

Host 仅将当前子线程真实成对的 built-in read_file / compact read(file) 成功完整文本结果作为来源事实。当前 RestrictedDispatcher 与 Root/TaskScoped 路由负责身份；plugin/MCP名字、模型 metadata、slices、父读取、披露、失败及截断结果不计。空文件有效。完整消息按有界页重建，热路径仅读新增消息；message epoch 改变后重放，不能从 context tail 获取完成事实。Core 仅消费 SourceCompletionHost / SourceCompletionSnapshot。

无工具文本且缺来源时，通过同一子运行反馈缺项；baseline 与 developer notices 在既有 checkpoint/messages 同事务落地，并以 correction ID 检查边界。notice 每条≤1000字符，长路径集合分块保留完整文本。反馈后无新增必要读取则 TASK_RESULT failed/source_requirements_unmet，remaining 完整列出缺项；新增必要读取才可继续纠正，次数受来源数及原预算共同限制。没有第二监督器、新表或子 TaskRecord。保留原跨 owner 执行拒绝，不新增 child 自动重启系统。

取消及 local/shared owner 的 round/tool/token 硬账本优先于缺来源失败；账本核验只读、不预留后撤销。读齐且有本轮非空最终回答允许原终态，包括恰好用完工具或最后总结轮；来源读齐但空/全空白答复 failed/empty_summary。无来源任务保持原行为。仅来源型 ChildResult 摘要取最后非空、无工具的 assistant 答复，避免旧计划挤掉纠正后的最终交付。

来源完成不证明分析正确、引用正确、Provider 实际输入完整覆盖或 verified。“读齐后只写计划”的非空答复仍由 P4 独审拒绝；代码不使用自然语言关键词判据。

## 经 Root 放行的关联预算修正

P1 将原 v7 父意图从 MODIFY 修为 ANALYZE 后，新任务自然初始软租约从 STANDARD(12/30)降到QUICK(4/8)。父披露+委派2轮与子原2轮已耗尽该租约，既有共享准入准确拒绝第三个子请求；P2直接4读的2轮也使父最终答复尚未实际发送。这是公开生产链路的真实耦合，不是来源门续预算的理由。

Root 明确放行：select_budget_lease 保留 explicit deep 优先，其后冻结 agent_topology=team 取 STANDARD；single/legacy analyze 仍 QUICK，其余仍 STANDARD。简单团队问答也受此 floor 影响，不声称复杂度判定。hard额度、child不能续父租约及已持久预算均不变。曾尝试的测试 start 后 get_or_create(...STANDARD)按冻结语义无效、仍失败；相关失败日志保留，最终测试已删除该注入，仅用自然公开启动。

现在原 v7 prompt 与原两轮流保留，明确声明四来源，再追加一条真实纠正回应。实际3个子请求后 source_requirements_unmet/四项remaining；不是 fake stream 耗尽导致的 execution_error。P2自然直接4读实际发送2个子请求、3个父请求，父最终答复流确实消费并 completed。

## 实际修改文件

- Core：AGENTS.md、source_completion.py（新增typed契约）、engine.py、_engine_run.py、_engine_turn.py、limits.py、tests/test_budget_lease.py。
- Orchestration：AGENTS.md、models.py（ChildRunRequest optional frozen sources）。
- Sessions：AGENTS.md、_shared_budget.py、_task_runtime_records.py（来源状态/只读预算/原子纠正接口）、_thread_content.py（复用事务内消息/node/index落地函数）。
- Host：chaos_agent/AGENTS.md、source_completion.py（新增事实adapter）、tools.py、subagents.py、child_runner.py、runtime_dispatcher_factory.py、application_context.py、child_result.py。
- 集成测试：tests/test_s16_source_completion.py、tests/test_source_completion_contract.py（新增21个反例测试）。
- 本报告及本阶段日志/离线wire物证。未改旧 owned、失败日志或冻结 v7 原来源。

以上是 P3 范围；git diff 中另有 P1/P2 的已审改动，不能将其全部算作 P3。

## 红绿物证与验证

先写4个公开生产红测试，required_sources 尚未入生产 schema 时全部失败（1 failure、3 errors，10.339s）；p3-red/*.json 保留原实际 parent body、events、child results。此前 P0 的零读取 completed 原失败仍在 p0-red.log / p0-wire-evidence，不改断言以制造通过。

中间失败也保留：p3-focused.log、p3-focused-final.log、p3-neighbor.log、p3-last-bounds.log。分别暴露共享 QUICK 租约、手工已绑定但尚未启动 child-local预算row，以及 factory 组装顺序变化；按根因修正，未增加等待或跳过测试。p3-green/保留中间wire，p3-green-final/为最终实际wire。

最终相关验证（实际 unittest 输出）：

| 范围 | 数量 | 耗时 | 结果/物证 |
|---|---:|---:|---|
| S16公开链路 + 来源反例 + budget lease | 38 | 83.733s | OK，p3-focused-accepted.log |
| Core Feature | 203 | 6.031s | OK，p3-core.log |
| Sessions Feature | 281 | 69.811s | OK，2 skipped，p3-sessions.log |
| Orchestration Feature | 24 | 1.246s | OK，p3-orchestration.log |
| 高风险邻近生产/授权/收集/取消/rewind/context/budget集成 | 38 | 13.416s | OK，p3-neighbor-accepted.log |
| 原子纠正及partial-factory cleanup局部修复 | 4 | 1.897s | OK，p3-repair.log |
| 新SQLite连接恢复来源与纠正baseline | 1 | 2.195s | OK，p3-reopened-recovery.log |

这些运行有重叠，不把合计当唯一测试ID数。最后增加的恢复断言经单项复验通过；全套和五平台CI未在本阶段执行。git diff --check 已通过。

命令均在唯一工作区运行，PYTHONPATH=src;.，Python为 C:/Users/Windows11/.codex/worktrees/next-version-s2-baseline/chaos-16-agent/.venv/Scripts/python.exe：

```text
-m unittest tests.test_s16_source_completion tests.test_source_completion_contract code_agent.core.tests.test_budget_lease -v
-m unittest discover -s src/code_agent/core/tests -p test_*.py
-m unittest discover -s src/code_agent/sessions/tests -p test_*.py
-m unittest discover -s src/code_agent/orchestration/tests -p test_*.py
-m unittest tests.test_production_child_factory tests.test_child_result_contract tests.test_subagent_integration tests.test_child_authorization_ceiling tests.test_child_execution_scope tests.test_runtime_partial_build_cleanup tests.test_prepared_context_assembly tests.test_rewind_lineage_integration tests.test_thread_budget_integration -v
-m unittest tests.test_source_completion_contract.SourceCompletionContractTests.test_resume_keeps_requirements_and_used_correction -v
```

已覆盖零读取、披露、部分/重复/错误路径/失败读取、截断、插件/MCP/metadata伪造、slices、父读不算、读齐、空文件、空答复、普通任务、纠正取消、本地与共享预算耗尽、最后合法回答、长notice完整性、事务rollback、旧binding、显式空冲突、新连接恢复、contexttail外旧证据与epoch失效。验证/分析质量保持独立。
