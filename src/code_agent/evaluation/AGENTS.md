# Evaluation
以可重放场景和确定性 grader 衡量 Agent 的安全、完成和恢复行为。

## 边界
- 负责：场景定义、临时工作区复制、scripted-model replay、确定性 grader、JSON 和 Markdown 指标汇总。
- 负责：直接检查文件、测试结果、任务状态、policy events 与 evidence，而不读取模型自述作为成功证据。
- 负责：固定 40 个场景按真实 Bug、跨文件契约、恢复/长期任务、安全/完成决策分组；修复类 fixture 必须能自动证明初始失败、隐藏 golden 可修复且公开与隐藏 verifier 均通过。
- 负责：隐藏 workspace、verifier 与 lifecycle oracle；执行器只看到任务提示和工作区，grader 独立检查完整变更集、真实 verifier 结果、动作/effect 去重、evidence generation、lineage、预算和策略事件。
- 负责：Runner 对每场景实施真实超时、隔离复制和确定性清理；隐藏 oracle 不复制进执行工作区，fixture 原目录永不执行或修改。
- 负责：只读分析必须在零写入下完成；安全拒绝必须出现所需策略证据；no-op、篡改测试、额外文件、假 verifier、自报 evidence、重放副作用和 stale evidence 均不能通过。
- 不负责：参与生产任务执行、保存 provider 凭据或在 fixture 原目录执行。
- 不负责：把 benchmark 成绩自动转化为 release 决策；发布门槛由集成层显式执行。
- 不负责：把 scripted replay 分数宣称为真实模型修复成功率，或把 hidden golden 应用于正式执行工作区。

## Units
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
