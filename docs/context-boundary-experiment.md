# Context boundary：实现与对照实验

本分支实现已确认的三层框架：API 能力约束硬上限、可配置换窗策略、独立累计任务预算。默认配置缺少 `context_policy` 时保留既有摘要路径；实验不会自动推广策略或修改私人 API 配置。

## 参数与行为

- API combined/input/output 分开表达。现有 profile `context_window` 作为配置的 combined cap；并非从模型名称推断可用容量。配置来源不等于服务端已验证的最大容量。
- 有效输入上限 = min(工作窗, combined - 实际 client 输出预留 - safety, 若配置 input 则 input - safety)。缓存输入不减小上下文占用，缓存 token 是 input 的子集。
- 初始工作窗 256000、准备阈值 75%、换窗阈值 87.5%、安全余量 16000、累计任务预算 5000000。输出预留沿用本地 profile 的 128000。策略不会自行扩窗或换模型。
- Workspace/rules/skills 构造完成后统一计数，主请求发送前再次检查。计数使用 tokenizer 估算和协议预留；API usage 为实测。未知模型保守回退 UTF-8 字节估算；未校准附件不进入此实验路径。
- 原始消息保持不变，在完整工具组之间产生窗口交接。普通 summary 和显式 boundary 读取相同范围、共用相同输出额度；显式交接要求目标、约束、已确认状态、证据锚点、失败尝试、未解决错误、待办、下一步字段。
- 交接和工作笔记始终是待核实的历史数据。保留最近真实用户请求，摘要不替代用户原文；来源发生变化则拒绝静默沿用旧窗口。
- SQLite 单事务提供窗口 CAS、幂等笔记、延后换窗请求、请求预算预留和结算。主/摘要共用独立预算；既有任务用量被承接；重启不能增大已冻结额度。请求中断且无 usage 保留预算预留。
- `context_history` 可分页读原始消息、搜索、列窗；`context_note` 可写/读/列/搜索笔记；`new_context` 在工具结果落盘后的下一轮生效。范围由 Host 绑定当前任务。

配置示例（在对应 provider profile 下显式启用）：

```toml
[providers.gpt56_sol.context_policy]
strategy = "boundary"
work_tokens = 256000
prepare_ratio = 0.75
rotate_ratio = 0.875
safety_tokens = 16000
task_tokens = 5000000
handoff_tokens = 8192
```

## 实验冻结约定

先完成独立 median pilot 和容量探测，再固定正式 10 个不同契约：Unicode 归一化、幂等账本、金额舍入、重试延迟、缓存过期、路径路由、区间合并、加权统计、CSV 转义、稳定分页。

正式测试是 **同一固定合成长历史现场上的真实 API 修复续跑**。前半段是确定性生成的审计记录和生产读文件工具得到的现场，不是模型从零执行的轨迹；每组后半段通过生产 AgentEngine、Provider adapter、上下文管理、动作策略及 Workspace 文件读写执行。它测量压缩后的恢复，不代表完整真实仓库长任务的端到端收益。

每个现场约 230k 估算输入，256k 工作窗，普通摘要/boundary 各一次，按任务交替 AB/BA 顺序串行运行。模型、low reasoning、输出上限、工具、原始历史、测试条件相同。普通摘要也读取完整来源，没有使用旧链路 8k 摘要输入裁剪来人为削弱基线。

- token：API input + output，含交接调用；缓存 input 单列，不重复相加。缺失 usage 记录为未知并保留预留。
- 重复读文件：同一路径、相同内容版本的再次 `read_file`；此前固定现场已读过也计入。文件改动后的新版本读取不计重复；历史检索另列。
- 压缩后错误恢复：初始独立 verifier 确认失败，实际提交至少一个窗口后，最终隐藏检查通过且保护文件不变。未换窗记录不进入恢复分母；失败和 API 异常保留，不能删除或替换。
- 隐藏输入/预期及参考代码均不复制进执行目录。公开审计样例与隐藏用例分离。成功由独立进程检查文件行为，不读模型自报。
- 每次最多 24 模型轮、80 工具调用、600 秒、5M task tokens；20 次理论 token 总上限 100M，实际应显著更少。账户真实货币单价不可由 model alias 推断；只报告 provider 实际 token。
- 正式任务开始后冻结代码、任务、阈值和验收；调试仅使用独立 pilot。没有自动改良基线、选优重跑或发布推广。

运行入口：`scripts/context_probe.py`（容量/计数）与 `scripts/context_long_benchmark.py`（paired continuation）。逐次 manifest、结果、事件、读文件 trace、SQLite 窗口/用量留在独立实验目录。
