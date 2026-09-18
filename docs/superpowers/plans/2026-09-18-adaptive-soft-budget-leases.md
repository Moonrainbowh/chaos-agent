# 自适应软预算租约实施计划

**目标：** 在现有 50 个模型回合、128 次工具调用等硬上限之内，为任务增加可恢复、可审计的 `quick`、`standard`、`deep` 软租约；任务只有取得 Host 可信的新进展才能自动续约，深任务最多再延伸一次至硬上限。

**架构：** Core 负责租约等级判定和生成有界的可信进展快照；Sessions 负责在现有 SQLite 写事务内原子执行“比较进展基线、续约、预留”，并把租约状态随 checkpoint、fork 和 rewind 一起保存；Interfaces 只投影持久事实。权限、沙箱、网络、Provider 能力披露和最终硬上限均不改变。

**技术栈：** Python 3.10+、`dataclasses`、`enum`、`hashlib`、`sqlite3`、现有 `unittest` 测试体系。

---

## 范围与非目标

- 保留 `EngineLimits` 的 50 回合、128 工具调用、每回合工具数、token、输出字符和 active-time 硬边界。
- `quick` 初始租约为 4 回合/8 工具，`standard` 为 12/30，`deep` 为 30/80；各值不得超过任务硬上限。
- 只读分析默认 `quick`，修改任务默认 `standard`；目标明确包含“深度、穷尽、全仓库、跨模块、deep、exhaustive、repository-wide、cross-module”等深度信号时使用 `deep`。
- `quick` 和 `standard` 在租约边界检测到新可信进展时续约至下一档阈值；`deep` 在 30/80 边界有新进展时只允许一次延伸至硬上限。
- 可信进展只来自 Host 已持久化事实：代码 generation 增加、不同 subject、不同验证 run/失败指纹、成功写入、不同只读结果、follow-up/steering 导致的任务修订。
- 模型正文、自述进度、重复读取、相同重复失败、执行前被拒绝的调用均不能续约。
- exact-repeat、连续五回合 tool-only、重复验证失败、repair、token 和 active-time 保护优先于租约续约。
- 不新增权限模式，不扩大网络或工作区范围，不让模型直接选择租约或构造进展证据。
- 不改变固定 40 场景评估目录；新增独立、确定性的租约 self-check。

## 文件地图

| 路径 | 职责 |
| --- | --- |
| `src/code_agent/core/limits.py` | 定义租约等级、阈值、可信进展快照和扩展后的 `TaskBudget`。 |
| `src/code_agent/core/protocols.py` | 扩展预算创建、预留协议，显式传递租约和进展。 |
| `src/code_agent/core/_engine_run.py` | 创建预算时选择租约；模型回合前构造可信进展快照。 |
| `src/code_agent/core/_engine_turn.py` | 处理租约续约/收敛事件，并把工具预留绑定同一进展快照。 |
| `src/code_agent/core/engine_actions.py` | 自动验证工具预留复用可信进展快照。 |
| `src/code_agent/sessions/_database.py` | v23 migration。 |
| `src/code_agent/sessions/_schema_validation.py` | 校验租约持久化列。 |
| `src/code_agent/sessions/_task_budget.py` | 在单事务内创建、续约和预留租约。 |
| `src/code_agent/sessions/_task_runtime_records.py` | 仓储协议适配。 |
| `src/code_agent/sessions/_workspace_model_values.py` | checkpoint 序列化租约状态。 |
| `src/code_agent/sessions/_checkpoint_budget.py` | fork/rewind 复制租约且保留累计使用量。 |
| `src/code_agent/interfaces/cost_control.py` | `/cost` 展示当前租约、阈值和续约次数。 |
| `src/code_agent/interfaces/terminal_state.py` | 投影 `category=lease` 的 renewed/converge 事件。 |
| `scripts/adaptive_budget_selfcheck.py` | 独立可重复的租约行为自检。 |
| Feature `AGENTS.md` | 同步 Core、Sessions、Interfaces、Verification、Evaluation 契约。 |

## 任务 1：建立纯 Core 租约契约

**文件：**
- 修改：`src/code_agent/core/limits.py`
- 修改：`src/code_agent/core/context_request.py`
- 修改：`src/code_agent/core/AGENTS.md`
- 修改：`src/code_agent/core/tests/test_engine_limits.py`
- 新建：`src/code_agent/core/tests/test_budget_lease.py`

- [ ] 定义 `BudgetLeaseTier(quick|standard|deep)`、固定阈值映射和 `select_budget_lease(contract)`；深度关键词只提升等级，不影响授权。
- [ ] 定义不可变 `TaskProgressSnapshot`，只接受有界、Host 生成的 generation、subject、验证、失败、写入、只读结果和 contract revision 指纹；提供稳定摘要，不保存工具正文。
- [ ] 扩展 `TaskBudget`：租约等级、当前回合/工具阈值、续约次数、最终延伸标志、基线进展摘要和最近续约原因；保持已有位置参数兼容，新增字段使用关键字默认值。
- [ ] 扩展 `budget_lease(...)` 数值投影，向上下文提供当前软阈值和续约次数，不暴露原始进展数据。
- [ ] 测试默认等级、深度关键词中英文边界、阈值被较小硬上限裁剪、非法持久值拒绝，以及旧 `TaskBudget(...)` 调用兼容。

验证：

```powershell
python -m unittest src/code_agent/core/tests/test_budget_lease.py src/code_agent/core/tests/test_engine_limits.py -v
```

## 任务 2：实现 v23 持久化与原子续约

**文件：**
- 修改：`src/code_agent/sessions/_database.py`
- 修改：`src/code_agent/sessions/_schema_validation.py`
- 修改：`src/code_agent/sessions/_task_budget.py`
- 修改：`src/code_agent/sessions/_task_runtime_records.py`
- 修改：`src/code_agent/core/protocols.py`
- 修改：`src/code_agent/sessions/tests/test_migrations.py`
- 修改：`src/code_agent/sessions/tests/test_edit_batch_migrations.py`
- 修改：`src/code_agent/sessions/tests/test_repository.py`

- [ ] v23 为 `task_budgets` 增加 tier、soft turn/tool limit、renewal count、final extension、progress baseline 和 last reason 列；为旧记录迁移为 `standard`，阈值裁剪到原硬上限。
- [ ] `get_or_create_task_budget` 接收首次创建时选定的 tier；已有记录永远使用持久 tier，不因恢复时的新输入改变。
- [ ] `reserve_task_budget` 接收当前 `TaskProgressSnapshot`，在一个 `BEGIN IMMEDIATE` 写事务内依次检查硬上限、软边界、可信进展、续约资格，再执行额度预留。
- [ ] 返回类型化预留结果，区分 `reserved`、`renewed`、`lease_exhausted` 和 `hard_exhausted`，使 Core 不通过字符串猜测原因。
- [ ] 续约仅在快照摘要不同于持久基线时发生；续约后原子更新阈值、计数、基线和原因。重复提交同一快照不得再次续约。
- [ ] 测试重启恢复、并发两次预留只有一次续约、无进展拒绝、重复失败不续约、不同失败/验证/读取可续约、deep 只延伸一次以及硬上限始终优先。

验证：

```powershell
python -m unittest src/code_agent/sessions/tests/test_migrations.py src/code_agent/sessions/tests/test_repository.py -v
```

## 任务 3：保证 checkpoint、fork 与 rewind 不重置租约

**文件：**
- 修改：`src/code_agent/sessions/_workspace_model_values.py`
- 修改：`src/code_agent/sessions/_checkpoint_budget.py`
- 修改：`src/code_agent/sessions/_task_budget.py`
- 修改：`src/code_agent/sessions/tests/test_checkpoint_fork.py`
- 修改：`src/code_agent/sessions/tests/test_lineage_budget.py`
- 修改：`src/code_agent/sessions/AGENTS.md`

- [ ] checkpoint payload 包含全部租约字段；旧 checkpoint 缺失字段时从当前持久预算安全回退。
- [ ] fork/rewind 使用当前、checkpoint 与 lineage 三者中的最大累计使用量，同时复制当前租约阈值、续约次数、最终延伸和基线，不能通过回退取得新租约。
- [ ] lineage 使用量继续只负责不可回退的累计量；租约元数据由当前 owner 预算权威保存，不在 lineage 中建立第二套状态机。
- [ ] 测试 checkpoint 后继续消耗再 fork、旧 payload 兼容、已延伸 deep fork 后不可再次延伸、以及失败事务不产生部分租约记录。

验证：

```powershell
python -m unittest src/code_agent/sessions/tests/test_checkpoint_fork.py src/code_agent/sessions/tests/test_lineage_budget.py -v
```

## 任务 4：把可信进展接入 Engine 收敛路径

**文件：**
- 修改：`src/code_agent/core/_engine_run.py`
- 修改：`src/code_agent/core/_engine_turn.py`
- 修改：`src/code_agent/core/engine_actions.py`
- 修改：`src/code_agent/core/_engine_convergence.py`
- 修改：`src/code_agent/core/tests/_engine_support.py`
- 新建：`src/code_agent/core/tests/test_engine_budget_lease.py`
- 修改：`src/code_agent/core/AGENTS.md`
- 修改：`src/code_agent/verification/AGENTS.md`

- [ ] Engine 创建任务预算时只依据冻结的 `TaskContract` 选择 tier；无 TaskRecord 的临时运行保持现有硬预算行为。
- [ ] 每次模型回合预留前，从 `TaskState`、最近已完成验证、Host 失败指纹以及已提升 follow-up/steering revision 构造快照；不读取 assistant 正文。
- [ ] 成功只读动作使用动作名、归一参数和结果的有界摘要形成新指纹；执行前拒绝和 exact repeat 不更新进展。
- [ ] 成功写入以新的 generation/subject 为主证据；验证以 run/outcome/failure fingerprint 为证据。
- [ ] 租约自动续约时发布 `TASK_BUDGET_WARNING {category: lease, phase: renewed}` 并注入一次 runtime notice；租约耗尽时发布 `phase: converge`，停止继续探索并进入现有完成/验证门，而不是抛出通用错误。
- [ ] exact-repeat、tool-only、repair、token、active-time 先执行；它们决定暂停/收敛时不允许租约覆盖。
- [ ] 自动 milestone/final verifier 的工具预留也经过软租约，但当前 generation 尚缺 required verifier 时，把 Host 规划的 verifier 视为必要的结构化进展，避免在验证门前错误截断。
- [ ] 覆盖 quick 无进展第 4 回合收敛、standard 新 generation 续约、重复读取不续约、不同读取续约、deep 单次延伸、恢复后基线不丢失、硬上限仍零额外调用。

验证：

```powershell
python -m unittest src/code_agent/core/tests/test_engine_budget_lease.py src/code_agent/core/tests/test_engine_stagnation.py src/code_agent/core/tests/test_engine_limits.py -v
```

## 任务 5：投影租约状态，不制造第二状态机

**文件：**
- 修改：`src/code_agent/interfaces/cost_control.py`
- 修改：`src/code_agent/interfaces/terminal_state.py`
- 修改：`src/code_agent/interfaces/AGENTS.md`
- 修改：`src/code_agent/interfaces/tests/test_cost_control.py`
- 修改：`src/code_agent/interfaces/tests/test_terminal_state.py`

- [ ] 扩展 `CostReport`，展示 tier、累计模型回合/工具调用、当前软阈值、硬上限、续约次数和最终延伸状态。
- [ ] `/cost` 明确区分“软租约”和“最终硬上限”，费用不可用语义保持不变。
- [ ] TerminalState 对 `lease/renewed` 显示短暂续约原因，对 `lease/converge` 显示收敛原因；忽略畸形字段，不能自行推导续约。
- [ ] 测试无价格、有价格、续约与收敛事件、窄状态行的有界文本。

验证：

```powershell
python -m unittest src/code_agent/interfaces/tests/test_cost_control.py src/code_agent/interfaces/tests/test_terminal_state.py -v
```

## 任务 6：增加独立评估自检和契约文档

**文件：**
- 新建：`scripts/adaptive_budget_selfcheck.py`
- 修改：`src/code_agent/evaluation/AGENTS.md`
- 修改：`README.md`

- [ ] 自检使用临时 SQLite 和确定性 Host 快照覆盖 quick/standard/deep、无进展、可信进展、重复进展、恢复、fork 与硬上限。
- [ ] 输出机器可读的通过/失败摘要，任一不变量失败时以非零退出；不调用模型、网络或真实工作区工具。
- [ ] README 说明软租约值、自动续约条件、硬上限、状态查询和不改变权限的边界。
- [ ] 固定 40 场景 catalog、ID、类别和 fingerprint 保持不变。

验证：

```powershell
python scripts/adaptive_budget_selfcheck.py
python -m unittest src/code_agent/evaluation/tests/test_catalog.py src/code_agent/evaluation/tests/test_gates.py -v
```

## 任务 7：全量回归与交付检查

- [ ] 运行 Core、Sessions、Verification、Interfaces 的完整测试目录。
- [ ] 运行仓库配置的 lint/type/build 门禁；若环境缺少可选依赖，保留原始失败并单独报告。
- [ ] 检查 `git diff --check`、schema v23 从每个旧版本迁移、无 `TBD`/`TODO` 遗留、固定评估目录未漂移。
- [ ] 确认未修改权限、沙箱、网络和 Provider 能力代码；确认无关未跟踪目录未被暂存。

验证：

```powershell
python -m unittest discover -s src/code_agent/core/tests -p "test_*.py" -v
python -m unittest discover -s src/code_agent/sessions/tests -p "test_*.py" -v
python -m unittest discover -s src/code_agent/verification/tests -p "test_*.py" -v
python -m unittest discover -s src/code_agent/interfaces/tests -p "test_*.py" -v
python scripts/adaptive_budget_selfcheck.py
git diff --check
```

## 提交边界

1. 中文实施计划。
2. Core 租约契约与纯函数测试。
3. Sessions v23、原子续约、checkpoint/fork/rewind。
4. Engine 可信进展接线与收敛事件。
5. Interfaces 投影、独立 self-check、README 与最终回归修复。

每次提交只暂存上述边界中的相关文件，不包含 `.playwright-cli/`、`.workbuddy/`、`artifacts/`、`logs/`、`output/` 或其他无关工作区内容。
