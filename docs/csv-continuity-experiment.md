# CSV 连续任务：多次换窗、代码变化与暂停恢复

[v2 案例升级](csv-continuity-v2-experiment.md) 已另建，增加真实中间失败、分阶段权限和独立覆盖维度；本页及 v1 历史结果保留。

第一版 `csv-continuity-v1` 是可重复的案例和评测控制器，复用现有 ScenarioRunner、DeterministicGrader 和隔离子进程 verifier。现已接入生产 AgentEngine、PersistentContextBuilder、SQLite History/Notes 与独立工作进程。离线脚本模型已验证四组宿主路径；真实 API 结果另存，不混入案例自检成绩。

[Luna 首轮真实 API 报告](experiments/csv-continuity-luna-api-pilot-01.md)：A–D 均通过，合计 267555 token；观察到实际记忆工具调用与 D 组进程恢复，但早期实现未被改版破坏，过期笔记与复杂未完成工作的覆盖仍不足。

## 固定任务

修复 CSV 批处理恢复时重复输出和累计统计错误。公开入口是 `run(source, output, checkpoint, limit=None)` 和既有 CLI。必须保留公开接口、旧 checkpoint v1 与已提交输出。输入在处理期间不变；checkpoint 可以落后于输出。ID 可重复，不能拿业务 ID 去重。

公开仓库有 batch、reader、storage、CLI、契约和一个会失败的恢复测试。隐藏测试覆盖断点落后、重复 ID、负值、带逗号字段、无新增记录的重跑、非法金额、CLI 兼容，以及改版后的空行与物理行号。参考实现和隐藏测试不导出到执行工作区。

代码变化固定为读取器跳过空行。checkpoint 的物理行号含义始终不变。旧的按记录序号恢复实现可通过改版前检查，改版后失败；这是自检中的一项正反对照。没有给模型预填笔记或结论。

## 四组计划

| 组 | 换窗 | 读取器改版 | Agent 进程退出重启 |
|---|---|---|---|
| A | 无 | 无 | 无 |
| B | 3 次 | 无 | 无 |
| C | 3 次 | 有 | 无 |
| D | 3 次 | 有 | 有 |

每组从相同公开文件开始，按 4、4、4、8 个完整模型/工具轮次分配四段工作。第一段与第二段后各换窗一次，第三段后再换窗一次；A 在对应位置仅经过控制器屏障。C/D 在第二个边界之后应用相同补丁；D 先持久化并确认进程退出，再改版、重启同一任务。

轮次数是上限，提前完成时等待下一个事件，不伪造工作轮次。切换必须在完整工具组后得到新窗口标识。若实际上未换窗、任务或持久存储换了身份、退出未确认、恢复仍是同一进程实例，驱动器拒绝该记录。失败后不按成绩更换事件位置或重跑覆盖结果。

这是受控边界实验，不是自然填满上下文的长任务。4/4/4/8 是首版 pilot 配置，正式 API 前应检查难度与预算是否足够；改变配置须另存版本。A/B 主要检查换窗增量，C/D 检查同一改版任务上的重启增量；B/C 同时增加工程难度，差异不能单独归因于记忆。每组一次仅供 pilot，后续需固定模型与推理档位、交替顺序并重复配对。

## 执行与验收

从仓库根目录运行：

```powershell
python -B scripts/continuity_benchmark.py --export F:/code-ai-chaos/chaos-16-context-experiments/csv-continuity-v1 --selfcheck --report docs/experiments/csv-continuity-v1-selfcheck.json
```

导出目录必须不存在；报告也拒绝覆盖。每组包含 `workspace/` 与同级的 `controller/`。Agent 只获得 workspace；controller 内保存未来事件计划和 C/D 外部补丁。导出不包含参考修复与隐藏测试。自检使用另外的临时工作区，不修好导出的坏代码。

`continuity_driver.ContinuityAdapter` 定义生产接入所需的 work/switch/pause/resume 四个动作。生产适配器需从 Host 真实事件取 task/store/window/process 身份、完整工具组状态与成功验证对应的工作区版本；不能从模型回复或模型写出的 JSON 取这些证据。进程实例标识应区别于可能重用的 PID。外层 ScenarioRunner 提供超时和最终独立验收，宿主适配器负责取消时终止自己的进程。

案例自检仍使用明确命名的 OfflineAdapter 测试 double：它应用参考或错误实现，执行真实 Python 测试，模拟窗口/进程回执。它只证明案例和判分能工作。

新增 ProcessContinuityAdapter 使用持久 JSON 控制通道启动独立 worker；worker 组合真实 AgentEngine、文件工具、History/Notes 与预算 client。工作阶段在完整工具组落盘后关闭生成器；换窗由生产 ContextBuilder 提交真实窗口记录。D 组确认旧进程退出后才应用补丁，新进程加载同一 SQLite thread。这里验证持久 thread 续跑，不等同 TUI 前台 TaskRecord 的完整生命周期。offline-host 模式仅替换模型输出，不能当成 API 或自主记忆收益证据。

四组同用 persistent 策略与 History/Notes；受控试验由宿主安排换窗，不向模型开放 new_context。API 采用显式 profile/model/effort，单次输出上限 8192、输入工作窗 64000、安全余量 2000，累计每组 300000 token、20 模型轮次、100 工具调用。遇未知 usage 保留预留与失败，不按零用量处理。

API 运行必须先冻结源码，避免共享工作区改动影响组间比较或 D 组重启。冻结不复制用户配置与凭据；模型配置在启动时读取并核对，修改模型名称会拒绝启动。示例（目标目录均须不存在）：

```powershell
.venv/Scripts/python.exe -X utf8 -B scripts/freeze_continuity_runtime.py F:/code-ai-chaos/chaos-16-context-experiments/continuity-runtime-new
.venv/Scripts/python.exe -X utf8 -B F:/code-ai-chaos/chaos-16-context-experiments/continuity-runtime-new/scripts/continuity_host_benchmark.py --mode api --profile gpt56_luna --model gpt-5.6-luna --effort low --task-tokens 300000 --output F:/code-ai-chaos/chaos-16-context-experiments/continuity-api-new
```

每组保留 `host/sessions.sqlite3`、真实 actions/events 日志、进程回执、最终工作区、独立验收和结果。未来复跑需固定同一源码快照、模型配置、案例与评分口径。

验收包含完整工作区修改范围、受保护文件、外部补丁保留、初始测试确实失败、最终独立冻结副本的公开/隐藏测试，以及最后工作区对应的 Agent 验证记录。独立验收通过不能替代 Agent 自己重新验证的证据。

报告保留各事件的工作区版本及 verifier 结果；History/Notes 调用次数只用于诊断，不是成功门槛。离线模式的模型、推理档位、API token 和记忆工具调用指标为 null。未来真实运行还须记录实际 provider/model/effort、输入/输出/缓存用量、任务预算、工具事件和暂停恢复原始证据。

## 版本使用

功能修改前后使用相同 v1 案例、控制器配置与验收。新增任务契约或调整正式阶段配置时创建 v2，保留 v1 结果。该案例独立于原有 40 场景，不更改其集合或历史成绩。本版是有针对性的多文件小型案例，通用性仍需更多真实工程任务验证。
