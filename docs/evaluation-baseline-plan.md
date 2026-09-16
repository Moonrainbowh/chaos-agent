# 编码 Agent 评测首轮基线计划

## 目的

建立通用编码能力基线，并与本项目的 `csv-continuity-v2` 恢复专项分开报告。首轮只做小规模 smoke/capability pilot，不把结果当作发布门槛或模型排名。

## 固定配置

- 模型：`gpt-5.6-luna`
- 推理档位：`high`
- Agent：当前 `chaos-agent` 生产宿主与工具策略
- 任务环境：每个任务独立、可重建、只读基线快照；公开基准优先使用其官方容器/沙箱
- 评分：任务测试结果为主；另记录工具调用、token、耗时、超时、基础设施失败和最终工作区
- 重复：首轮每个任务 2 次；不使用 Test 结果选择提示词、模型或实现

## 首轮任务范围

1. SWE-bench Verified：先选 3 个小型、依赖稳定的 Python 任务，验证真实仓库补丁和回归测试链路。
2. Terminal-Bench：先选 2 个不要求 GPU 的代码/测试任务，验证终端沙箱和端到端验收链路。
3. Continuity v2：保留 A–D 专项结果，单独统计切窗、改版、重启和记忆证据。

SWE-bench/Terminal-Bench 作为通用基线，不能替代 continuity 专项；continuity 也不能直接当作公开基准成绩。

## 执行门槛

- 先确认 Docker 引擎、官方评测工具版本、数据集版本和任务 ID。
- 先用一个已知参考解和一个已知失败变体验证评分器。
- 先完成 1 个任务的端到端 smoke run，再扩展到其余任务。
- 每次运行使用新的 `run_id` 和输出目录；保留原始日志，不覆盖失败记录。
- 区分模型/Agent 失败、任务评分失败和基础设施失败；未知 token 用量保留为 unknown。

## 当前状态

SWE-bench 5.0.2 已安装到项目 `.venv`，Docker Desktop Linux engine 可用。首个 `astropy__astropy-12907` gold smoke 首次判为 unresolved，原始日志显示 Windows 将官方 `/eval.sh` 以 CRLF 写入，Linux 容器因此把命令和路径解析为带 `\r` 的字符串，属于评测器跨平台换行缺陷而非任务失败。

在本地评测器写入脚本时强制 `newline="\n"` 后，同一实例以新 `run_id` 重跑，耗时 105.96 秒，结果为 `resolved=1`、`infra_failure=0`、`error=0`。后续 Windows 评测必须保留该换行修复，并保留 `logs/run_evaluation/<run_id>/` 下的 `eval.sh` 与 `test_output.txt` 作为诊断证据。

随后使用同一实例构造了错误变体（把 `right` 矩阵恢复为全零），运行 103.84 秒并得到 `resolved=0`；原始输出显示 9 个 separability 测试失败、6 个通过，证明评分器能区分正确补丁与失败补丁。结果保存在 `artifacts/swebench/negative-control.negative-smoke-20260911.json`。

### Agent Harness 首次运行

按 `gpt-5.6-luna` + `high` 在独立 Astropy checkout 启动 `astropy__astropy-12907` 一次。运行到第 15 个 model turn 时宿主输出 `TypeError`，尚未生成 patch 或 verification，因此该次应记为 `harness_failed_before_patch`，不能记为 resolved/unresolved。事件统计为 15 turns、18 tool calls、input 113,560、cached input 44,544、output 1,096 tokens；重复的 `load_tool_contract` 和宽范围 `read_file` 已被记录为无效重复/上下文放大信号。该次没有文件修改、L0/L1/L2 语义层命中或 verification 结果，摘要在 `artifacts/swebench/agent-runs/astropy__astropy-12907/summary.json`。

在修复该 Harness 崩溃与宽读取问题前，暂停其余两题，避免把不同宿主状态混进同一轮比较。

后续诊断确认熔断器已修复后，Luna/High 仍有两次 Provider 流失败：一次在首轮 model turn，另一次在第 4 轮，均为 `ProviderProtocolError`，没有形成 patch。该类结果记录为 `provider_infrastructure_failure`，不记作 Agent unresolved。`circuit_breaker_result` 现在使用 `plain()` 序列化冻结参数，并有回归测试覆盖嵌套参数；核心相关测试 13 项通过。

## 通过后的扩展顺序

首轮 smoke run 通过后，再扩大任务数量和重复次数；随后比较公开基准基线与 continuity v2 的失败模式。只有在重复结果稳定、评分器和基础设施失败率可解释时，才考虑把它们纳入持续回归。

### 三任务 Agent 基线（GPT-5.6 Luna / High，单次）

三题均使用独立 checkout，最终 patch 通过 Docker 中 SWE-bench Verified 官方验收：

| 任务 | 官方结果 | Agent 过程 | 主要证据 |
|---|---|---|---|
| `astropy__astropy-12907` | resolved=1 | 先后经历 Harness 序列化崩溃与 Provider 流失败；后续运行生成正确 patch，Agent 自检因 checkout 缺少 `hypothesis` 未完成 | `gpt-5.6-luna-high.agent-luna-high-astropy-12907.json` |
| `astropy__astropy-13033` | resolved=1 | 达到 50 turn budget 后停止，但已生成可验收 patch；官方 scorer 通过 | `gpt-5.6-luna-high.agent-luna-high-astropy-13033.json` |
| `django__django-10914` | resolved=1 | 过程出现较多探索和重复读取，达到较长运行后保留 patch；官方 scorer 通过 | `gpt-5.6-luna-high.agent-luna-high-django-10914.json` |

本轮结论是“最终 patch 基线 3/3 resolved”，不能等同于“Agent 过程 3/3 正常收敛”。后续应以事件 JSONL 补齐 turns、tool calls、Provider usage、耗时、文件读取、L0/L1/L2 命中、verification 和无效重复调用字段，并把 turn budget、依赖缺失、ProviderProtocolError 分开统计。
