# CSV 连续任务：csv-continuity-v2 api 结果

本轮是真实 `gpt-5.6-luna / high` API pilot，使用冻结运行时 `csv-continuity-v2-luna-high-runtime-01`。四组各一次，保留所有原始成绩，没有为通过率补跑。

**结论：A/B 严格验收通过；C 最终代码和笔记纠正通过，但发生一次工具误用，严格验收不通过；D 在最终修复阶段遇到 Provider 协议错误，未完成评估。** 不把 D 归因于模型无法解题，也不把进程恢复已发生解释为完整恢复任务已通过。

已知 Provider 用量合计 329564 token；另有 1 次请求 usage 未知，其 16764 token 是预算预留，不是实际消费，不能与已知用量相加当成真实总量。

## 逐条证据审阅

本节为主 Agent 对保留工具记录、阶段源码和最终验证的核对，不是独立人工专家评审。下列行号指各组 `host/actions.jsonl`。

| 组 | 具体证据 | 结论与边界 |
|---|---|---|
| A | 14 行写入初次修复笔记，15 行改版后读取，19 行当前验证实际报 Record 解包错误，20 行追加旧验证过时及待修复判断；25 行最终验证通过 | 笔记与当前代码对应，但本组无切窗 |
| B | 9/11 行首次跨窗笔记写入/读取；30 行记录新版接口及待修复项；35/36 行最后一次切窗后读回诊断及旧修复笔记，43 行追加最终适配与验证结果 | 三次切窗后完成；25 行 History 读取的是当前阶段提示，不能算有效旧证据检索 |
| C | 20 行旧 tuple 契约，21/34 行改版后读旧笔记，26 行新版失败，42 行明确更正为 Record，43 行当前验证通过，47 行记录最终验证状态 | 显式笔记探针的语义纠正有证据；46 行误用工作区 replace_text 修改 Notes 路径，被拒绝后改用 notes_write_file，因此严格行为门失败 |
| D | pause receipt 退出码 0，新旧两个进程实例共享 task/store；23 行新进程读取旧 reader-contract.md，28 行新版验证失败；第 4 阶段第 3 次模型请求中断 | 重启和旧笔记读取已发生，最终修复及笔记更新未完成 |

A/B 使用自选笔记文件名，且有 append 更新；下方冻结自动指标只识别固定 `reader-contract.md` 写入/读取/重写链。因此其自动 `note_lifecycle=not-covered` 不代表没有使用笔记。原始自动字段不改分，本节单独说明观察到的行为。C 的工具错误也未因恢复成功而删除。

D 的 `worker-error.json` 仅保存 `ModelStreamError → ProviderProtocolError` 类型链，现有日志不足以确定是响应截断、格式异常还是具体解析分支，不能宣称已定位供应商根因。中断也导致驱动器未回传完整最终 journal；已有阶段快照、工具日志、SQLite 与重启 receipts 均保留。全套命令退出码为 1，符合 C/D 未通过的冻结验收口径。

## 冻结自动汇总

模型配置标签：`gpt-5.6-luna`；推理档位：`high`；运行模式：`api`。
每组累计上限 300000 token，20 模型轮次、100 工具调用。四组同用 persistent 策略；单任务、每组一次，仅为 pilot。

| 组 | 最终验收 | 换窗 | 模型轮次 | token | History 调用 | Notes 调用 |
|---|---|---:|---:|---:|---:|---:|
| A | 通过 | 0 | 15 | 117677 | 0 | 5 |
| B | 通过 | 3 | 19 | 84262 | 2 | 9 |
| C | 通过 | 3 | 19 | 80746 | 0 | 11 |
| D | 未通过 | 3 | 15 | 46879 | 1 | 8 |

## 挑战覆盖（独立于最终通过）

| 组 | 旧修复被破坏 | 未完成工作跨界 | 挑战验收 | 越界尝试 | 笔记生命周期 | 笔记语义 | 整链状态 |
|---|---|---|---|---:|---|---|---|
| A | True | True | True | 0 | not-covered | not-covered | review-required |
| B | True | True | True | 0 | not-covered | not-covered | review-required |
| C | True | True | False | 1 | observed | review-required | not-covered |
| D | False | False | False | 0 | not-covered | not-covered | not-covered |

已发生笔记读写和变化仅证明生命周期；语义纠正必须逐条核对 Notes、历史来源与当前代码。review-required 不等于整链通过。阶段快照保存在各组 stages/，不包含隐藏 oracle。

合计已知用量：329564 token；usage 未知请求：1。缓存输入已包含在 input 中，不重复累加。

## 分阶段行为

写文件列只计成功的 write_file/replace_text；验证列只计真实公开 verifier 成功。记忆工具次数包含失败尝试，不作为成功门槛。

| 组/阶段 | 模型调用 | 工具调用 | 写文件 | 验证成功 | History | Notes |
|---|---:|---:|---:|---:|---:|---:|
| A/1 | 3 | 10 | 0 | 0 | 0 | 2 |
| A/2 | 4 | 4 | 1 | 1 | 0 | 1 |
| A/3 | 3 | 6 | 0 | 0 | 0 | 2 |
| A/4 | 5 | 5 | 2 | 1 | 0 | 0 |
| B/1 | 3 | 9 | 0 | 0 | 0 | 1 |
| B/2 | 7 | 11 | 1 | 1 | 0 | 3 |
| B/3 | 3 | 10 | 0 | 0 | 2 | 1 |
| B/4 | 6 | 13 | 1 | 1 | 0 | 4 |
| C/1 | 3 | 10 | 0 | 0 | 0 | 1 |
| C/2 | 6 | 10 | 1 | 1 | 0 | 3 |
| C/3 | 3 | 9 | 0 | 0 | 0 | 1 |
| C/4 | 7 | 18 | 1 | 1 | 0 | 6 |
| D/1 | 3 | 10 | 0 | 0 | 0 | 1 |
| D/2 | 6 | 12 | 1 | 1 | 0 | 3 |
| D/3 | 3 | 7 | 0 | 0 | 1 | 1 |
| D/4 | 3 | 9 | 0 | 0 | 0 | 3 |

## 验收边界

最终代码由独立克隆内的公开及隐藏 verifier 检查，同时要求 Agent 自己的成功验证对应最终工作区。D 组的退出码与新旧进程身份保留在 receipts。这里是生产 Engine/Context/SQLite thread 续跑，不是完整 TUI TaskRecord 生命周期验收。

v2 所有组接受相同改版；A/B 对比切窗，B/C 对比自然记忆和明确笔记要求，C/D 对比重启。四组均使用 persistent，不能据此宣称优于 summary。

完整证据：[结果 JSON](F:/code-ai-chaos/chaos-16-context-experiments/csv-continuity-v2-luna-high-api-01/results.json)；[冻结配置](F:/code-ai-chaos/chaos-16-context-experiments/csv-continuity-v2-luna-high-api-01/manifest.json)。

D 失败原因：trusted execution trace is incomplete; unexpected task status; infrastructure failure: executor failed: RuntimeError: worker failed: ModelStreamError; trusted interaction budget missing; trusted verifier failed: public; trusted verifier failed: hidden; missing successful agent verification for final workspace; unknown API usage
