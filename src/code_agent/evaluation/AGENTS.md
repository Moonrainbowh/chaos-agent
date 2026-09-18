# Evaluation
以可重放场景和确定性 grader 衡量 Agent 的安全、完成和恢复行为。

## 边界
- 负责：独立版本 `csv-continuity-v2` 的分阶段诊断/修复、相同依赖改版与切窗/重启对照；在真实阶段快照上检查旧修复改版前通过、改版后失败、最终修复通过。自然记忆与显式笔记探针分开；未触发的难题标记未覆盖，笔记语义正确性不得由调用次数推断。保留 v1 契约和历史成绩。
- 负责：独立的 `csv-continuity-v1` 多阶段案例及 A/B/C/D 事件计划；冻结外部补丁、公开契约与隐藏行为验收。离线参考实现自检不计作真实模型、换窗或进程恢复成绩，不改动既有 40 场景集合。
- 负责：场景定义、临时工作区复制、scripted-model replay、确定性 grader、JSON 和 Markdown 指标汇总。
- 负责：直接检查文件、测试结果、任务状态、policy events 与 evidence，而不读取模型自述作为成功证据。
- 负责：固定 40 个场景按真实 Bug、跨文件契约、恢复/长期任务、安全/完成决策分组；修复类 fixture 必须能自动证明初始失败、隐藏 golden 可修复且公开与隐藏 verifier 均通过。
- 负责：隐藏 workspace、verifier 与 lifecycle oracle；执行器只看到任务提示和工作区，grader 独立检查完整变更集、真实 verifier 结果、动作/effect 去重、evidence generation、lineage、预算和策略事件。
- 负责：Runner 对每场景实施真实超时、隔离复制和确定性清理；隐藏 oracle 不复制进执行工作区，fixture 原目录永不执行或修改。
- 负责：只读分析必须在零写入下完成；安全拒绝必须出现所需策略证据；no-op、篡改测试、额外文件、假 verifier、自报 evidence、重放副作用和 stale evidence 均不能通过。
- 不负责：参与生产任务执行、保存 provider 凭据或在 fixture 原目录执行。
- 不负责：把 benchmark 成绩自动转化为 release 决策；发布门槛由集成层显式执行。
- 不负责：把 scripted replay 分数宣称为真实模型修复成功率，或把 hidden golden 应用于正式执行工作区。
- 独立 `scripts/adaptive_budget_selfcheck.py` 只用临时 SQLite 和确定性 Host 进展快照检查软租约、恢复、fork 与硬上限；它不调用模型、网络或真实工作区工具，也不改变固定 40 场景 catalog。

## Units
- `ExperienceTaskRecord`, `ExperienceEvaluationMetrics`, `render_experience_metrics`: 记录真实工程任务的可理解性与完成质量（完成、真实验证、用户介入、重试、误报完成、耗时、token、工具调用及失败来源）；只接受受信 runner/grader 事实，不把 replay 分数或模型自述当作真实任务成功率，也不把 Provider/基础设施失败计作模型失败。
- `TrustedTraceRecorder.record_user_interventions`, `record_retries`, `record_model_tokens`: 接收生产适配器提供的可选真实用量和交互事实；未采集保持 unknown，不以 0 冒充真实用量。
- `continuity_v2_fixture/plan/driver/selfcheck/metrics`：冻结读取器 tuple→Record 改版、四阶段权限和 A/B 自然记忆、C/D 显式笔记探针对照 | 隔离阶段快照与隐藏行为验收 | 最终正确与挑战覆盖分开；已兼容实现必须得到未覆盖；笔记写入/读取/变化仅证明生命周期，语义仍需审阅
- `continuity_fixture`、`continuity_oracle.scenario`: 冻结 CSV 任务、外部读取器补丁和独立验收 | 生成公开 fixture | 参考修复和隐藏测试仅供隔离自检
- `continuity_plan.events/manifest`、`continuity_driver.drive`: A/B/C/D 固定阶段和宿主适配器协议 | 受信控制器在工具组落盘后换窗/改版/重启 | receipt 必须来自真实宿主；生产进程适配器由 code_agent_win/continuity_* 组合，离线 double、真实宿主脚本模型与真实 API 分别报告
- `continuity_selfcheck.selfcheck`: 复用 ScenarioRunner、DeterministicGrader 和子进程 verifier 验证参考实现及错误变体 | 临时工作区与子进程 | 所有成绩标记 offline-scripted-reference，API/History/Notes 指标为 null
- `long_context_cases`: 固定 10 个不同代码契约与长历史恢复现场 | 生成隔离 fixture | 前史为确定性现场，后续 AgentEngine/API 实跑；不可宣称从零端到端长任务成绩
- `long_context_metrics`: 分别核算主请求/交接/缓存、稳定内容重复读取、边界后注入错误恢复与独立验收 | 读取受信日志/最终文件 | 无边界或 API 失败保留为无效/失败，不补成功分数
- `WorkspaceOracle`、`VerifierOracle`、`LifecycleOracle`: 冻结完整变更集、可信验证与生命周期约束 | 无副作用 | 源码等价性由 verifier 判定，只有状态协议文件使用 exact
- `Scenario`、`ScenarioPrompt`、`ScenarioResult`: 分离隐藏预期、执行器输入与不受信返回值 | 无副作用 | golden 仅供 fixture 自检且不进入 prompt
- `bugfix_fixture(...)`、`multifile_fixture(...)`、`recovery_fixture(...)`、`safety_fixture(...)`: 确定性生成真实坏源码、公开测试和隐藏 verifier | 无副作用 | hidden 文件只存在于 oracle
- `TrustedTraceRecorder`、`TrustedExecutionTrace`: 接收生产 adapter 的受信任务、策略、预算、生命周期事件 | 内存状态 | 未注入或未 seal 时 fail closed，绝不从 `ScenarioResult` 回填
- `checked_relative(...)`、`contained_path(...)`: 拒绝 drive/UNC/root/父级路径并证明 materialize containment | 读取路径元数据 | 不穿越 symlink/reparse parent
- `workspace_manifest(...)`、`HarnessObservation`: 记录 baseline/final 哈希、可信 trace/verifier、越界写、隔离、终止、超时与清理状态 | 读取临时文件 | 文本换行归一化后比较
- `SubprocessVerifier`、`run_trusted_verifier(...)`: 预检工具链，在独立克隆中注入隐藏检查并无 shell 执行 | 临时进程与文件系统 I/O | 超时终止进程树，不污染执行工作区
- `ProcessScenarioExecutor(trusted_adapter_argv=...)`: 通过有界 stdin/stdout envelope 对接宿主可信生产 adapter，并提供可终止进程树边界 | 子进程 I/O | 禁止直连 raw model；超限首字节立即终止进程树
- `ScenarioRunner.run(scenario, execute)`: 删除 baseline hidden clone 后才执行，从执行结束后的冻结 snapshot 验证并确定性清理 | 临时文件系统与进程 I/O | in-process 无法终止时保留现场并 fail closed
- `DeterministicGrader.grade(scenario, result, observation)`: 仅以受信 observation 审计完整变更、验证、预算、策略、generation、lineage 与 effect 去重 | 无额外副作用 | `ScenarioResult` 自报不能构成正向证据
- `EvaluationMetrics`、`render_markdown_report`: 汇总可重复 JSON 结果和人类可读报告 | 无副作用 | 不保存 provider 凭据或原始秘密
- `BenchmarkRecord`、`ReplayBenchmark.run(...)`、`ReplayBenchmarkResult.write_reports(...)`: 以单一 record 源执行并导出固定 40 场景 | 临时文件系统 I/O | 报告含 verifier、digest、changed、timeout、cleanup 与 infrastructure
- `corpus_fingerprint(...)`、`evaluate_hard_gates(...)`: 审计 canonical corpus 指纹/ID/配额、重算 grade 与指标 | 无副作用 | 人工 records、空 grades 或不一致统计不能通过
- `adaptive_budget_selfcheck.py`: 机器可读地覆盖 quick/standard/deep、无进展、可信/重复进展、重启、fork 与最终硬上限 | 临时 SQLite I/O | 任一不变量失败以非零退出；不计入 benchmark 分数
