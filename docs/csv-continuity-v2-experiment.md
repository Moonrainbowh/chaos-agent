# CSV 连续任务 v2：覆盖未完成工作与失效证据纠正

v1 保留为机制回归。v2 是独立版本，修正首轮 Luna pilot 中早期完成、改版未破坏旧解法、目录查询被误读为记忆能力的问题。已完成案例自检、脚本模型宿主验证及一次 [Luna/high 真实 API pilot](experiments/csv-continuity-v2-luna-high-api-01.md)：A/B 通过，C 有工具误用，D 因 Provider 协议错误未完成。生产 History/Notes 策略未修改。

## 相同任务、不同干预

公开目标仍是 CSV 断点续跑：保留 `run(source, output, checkpoint, limit=None)`、CLI、append-only 输出、物理行号、checkpoint v1 及累计统计。读取器是内部依赖，所有组在第二阶段之后接受同一迁移：三元组改为不可直接解包的 `Record(line, identity, amount)`，并支持跳过空行。batch 必须继续调用当前 `reader.records`。

| 组 | 同一依赖改版 | 切窗 | 记忆要求 | 进程重启 |
|---|---|---|---|---|
| A | 有 | 无 | 自然使用 | 无 |
| B | 有 | 3 次 | 自然使用 | 无 |
| C | 有 | 3 次 | 显式维护和读取笔记 | 无 |
| D | 有 | 3 次 | 显式维护和读取笔记 | 有 |

A/B 检查切窗影响，B/C 检查明确笔记要求的影响，C/D 检查相同笔记探针下的重启影响。四组共享迁移难度；不得与 v1 同名组直接合并统计。没有 summary 对照，不据此推断 persistent 优于 summary。

## 四阶段与真实未完成工作

1. **诊断，最多 3 轮。** 允许读文件、验证、History/Notes；宿主禁止修改工作区。边界一后仍有原始修复尚未执行。
2. **初次修复，最多 7 轮。** 允许修改 batch 和新增测试，保留既有公开测试。C/D 明确要求模型自行写 `reader-contract.md`，记录观察到的接口、证据和未完成工作；不预填任何结论。
3. **迁移后诊断，最多 3 轮。** 在固定边界迁移依赖；D 先确认旧进程退出，再改版并启动同一 SQLite thread。模型检查当前源码和过往判断，暂不能改代码。C/D 明确要求读取早期笔记。边界三后留下迁移修复。
4. **最终修复，最多 7 轮。** 完成迁移适配、独立公开验证；C/D 更新笔记中的过时结论及验证状态。

所有边界均在完整工具组后发生；不根据模型成绩移动边界或挑选补丁。诊断权限使案例成为受控分阶段任务，不等于自然耗尽上下文时的长任务。每组上限沿用 20 模型轮次、100 工具调用和 300000 token。

## 覆盖必须有证据

宿主保存五个工作区快照，在额外隔离副本中执行隐藏行为检查：首次诊断后、改版前、改版后、迁移诊断后和最终版本。隐藏测试不进入 Agent 工作区或保存的公开快照。关键链条必须是：**改版前通过 → 相同 batch 在改版后失败 → 第三边界仍有失败 → 最终通过**。工具链故障或超时单列基础设施失效。

实际实现如果提前兼容了新版，最终正确性可以通过，但旧解失效这一项必须是 `not-covered`。离线负对照专门验证这一点。初次修复未成功也不能算已覆盖旧解纠正。

报告分开保留：

- `passed`：最终公开/隐藏行为、工作区范围、宿主身份及最终版本的模型验证证据。
- `challenge_coverage_passed`：最终通过、上述阶段链条成立、没有被宿主记录的越界修改尝试。
- `constraint_attempts`：诊断期写入或修改保护文件的尝试，即使被阻止也记录。
- `note_lifecycle`：非空早期笔记、改版后且覆盖旧笔记前的成功读取、后续内容变化。仅列目录、失败读取、空笔记或重复原文均不得分。
- `note_semantics`：生命周期观察到后仍是 `review-required`，调用次数不证明语义纠正。
- `full_chain_status`：自动路径最多到 `review-required`，缺项则 `not-covered`，绝不以最终通过冒充整链覆盖。

语义审阅需要对照 `host/actions.jsonl`、SQLite History/Notes 与 `stages/`：早期笔记是否反映旧代码，恢复后是否读取旧内容，更新是否识别旧接口失效并指向新版事实，未完成项是否在最终代码和验证中闭合。脚本笔记只能验接线，真实模型运行后才进行此审阅。A/B 未自发产生笔记时如实标未覆盖，不强行补写。

## 验证与复跑

案例自检（输出路径必须不存在）：

```powershell
.venv/Scripts/python.exe -X utf8 -B scripts/continuity_v2_benchmark.py --selfcheck --export F:/code-ai-chaos/chaos-16-context-experiments/csv-continuity-v2-new --report docs/experiments/csv-continuity-v2-selfcheck-new.json
```

脚本模型完整宿主：

```powershell
.venv/Scripts/python.exe -X utf8 -B scripts/continuity_host_benchmark.py --mode offline --fixture-version v2 --output F:/code-ai-chaos/chaos-16-context-experiments/csv-continuity-v2-host-new
```

真实模型复跑前冻结最终源码，再从快照执行同一命令，改为 `--mode api --profile gpt56_luna --model gpt-5.6-luna --effort high`。用户指定后，连续任务评测默认使用 Luna/high；v1 已有 low 成绩保留原标记。四组最大累计预算为 1200000 token，实际用量以 Provider 回执为准；预算耗尽、接口失败、缺覆盖都保留，不自动扩预算重跑。

报告使用 `scripts/report_continuity_run.py <run-root> <new-report.md>`；明确区分 offline 与 api。保留 v1 历史报告，不覆盖或重算其通过率。

## 本次完成证据

- [离线案例自检](experiments/csv-continuity-v2-selfcheck.json)：四组参考流程通过；不适配迁移、提前双版本兼容、缺最终验证三种反例均被覆盖门拒绝。其中提前兼容仍通过最终行为检查，证明两种分数已经分开。
- [冻结源码的四组宿主报告](experiments/csv-continuity-v2-offline-host-02.md)：真实 Engine/Context/SQLite 和进程宿主，模型输出为离线脚本。四组挑战覆盖通过，D 组真实退出重启；笔记语义和整链状态保留为待审阅。
- Windows 本地验证：Evaluation 42 项、根集成 444 项全部通过；后续笔记读取顺序边界与 CLI v1/v2 返回状态分别做定向检查。这些离线验证不含全项目各 Feature 发布门禁或真实 API，后续 API 结果另列。
- 宿主证据使用 `csv-continuity-v2-offline-runtime-02` 快照；之后仅完善本地报告标签与 CLI 覆盖返回状态，后者已定向检查。真实 API 应重新冻结最终源码。
- 后续真实 API 使用 `csv-continuity-v2-luna-high-runtime-01` 新快照和 Luna/high，结果及逐条笔记审阅见上述独立报告；未补跑覆盖 C 的工具误用或 D 的协议中断。未来此系列默认模型为 Luna/high。
