# Evaluation
以可重放场景和确定性 grader 衡量 Agent 的安全、完成和恢复行为。

## 边界
- 负责：场景定义、临时工作区复制、scripted-model replay、确定性 grader、JSON 和 Markdown 指标汇总。
- 负责：直接检查文件、测试结果、任务状态、policy events 与 evidence，而不读取模型自述作为成功证据。
- 不负责：参与生产任务执行、保存 provider 凭据或在 fixture 原目录执行。
- 不负责：把 benchmark 成绩自动转化为 release 决策；发布门槛由集成层显式执行。

## Units
- `Scenario`、`ScenarioExpectedOutcome`、`ScenarioResult`: 冻结可重放任务、隐藏 oracle 和执行结果 | 无副作用 | fixture 原目录永不作为执行工作区
- `ScenarioRunner.run(scenario, execute)`: 临时复制 fixture 并调用 injected scripted runner | 临时文件系统 I/O | runner 不接收 fixture 原始路径
- `DeterministicGrader.grade(scenario, result)`: 直接比对文件、task status 与 policy events | 读取临时工作区 | 不读取模型最终回答
- `EvaluationMetrics`、`render_markdown_report`: 汇总可重复 JSON 结果和人类可读报告 | 无副作用 | 不保存 provider 凭据或原始秘密
- `fixed_replay_catalog(...)`: 定义 Python、Node、.NET、Windows 恢复、安全与只读共 40 个固定 replay 场景 | 无副作用 | executor 只能得到 `ScenarioPrompt`，hidden expected 仅供 grader 使用
- `ReplayBenchmark.run(...)`: 在临时 fixture 复制上执行固定场景并导出 JSON/Markdown | 临时文件系统 I/O | 只把 grader 结果计入指标，不读取模型自述
- `ReplayBenchmarkResult.write_reports(...)`: 写出 replay JSON 和 Markdown 产物 | 文件系统 I/O | 只写调用方显式提供的目录，不包含模型原始输出或凭据
- `evaluate_hard_gates(...)`: 审计固定场景数量、越界执行、假完成、evidence 完整性和 grader 失败 | 无副作用 | 任一硬门槛失败不得被包装为稳定发布
