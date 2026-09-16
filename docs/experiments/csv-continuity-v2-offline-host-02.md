# CSV 连续任务：csv-continuity-v2 offline 结果

模型配置标签：`gpt-5.6-luna`；推理档位：`low`；运行模式：`offline`。
每组累计上限 300000 token，20 模型轮次、100 工具调用。四组同用 persistent 策略；单任务、每组一次，仅为 pilot。

| 组 | 最终验收 | 换窗 | 模型轮次 | token | History 调用 | Notes 调用 |
|---|---|---:|---:|---:|---:|---:|
| A | 通过 | 0 | 8 | 4480 | 4 | 6 |
| B | 通过 | 3 | 8 | 4480 | 4 | 6 |
| C | 通过 | 3 | 8 | 4480 | 4 | 6 |
| D | 通过 | 3 | 8 | 4480 | 4 | 6 |

## 挑战覆盖（独立于最终通过）

| 组 | 旧修复被破坏 | 未完成工作跨界 | 挑战验收 | 越界尝试 | 笔记生命周期 | 笔记语义 | 整链状态 |
|---|---|---|---|---:|---|---|---|
| A | True | True | True | 0 | observed | review-required | review-required |
| B | True | True | True | 0 | observed | review-required | review-required |
| C | True | True | True | 0 | observed | review-required | review-required |
| D | True | True | True | 0 | observed | review-required | review-required |

已发生笔记读写和变化仅证明生命周期；语义纠正必须逐条核对 Notes、历史来源与当前代码。review-required 不等于整链通过。阶段快照保存在各组 stages/，不包含隐藏 oracle。

合计已知用量：17920 token；usage 未知请求：0。缓存输入已包含在 input 中，不重复累加。

## 分阶段行为

写文件列只计成功的 write_file/replace_text；验证列只计真实公开 verifier 成功。记忆工具次数包含失败尝试，不作为成功门槛。

| 组/阶段 | 模型调用 | 工具调用 | 写文件 | 验证成功 | History | Notes |
|---|---:|---:|---:|---:|---:|---:|
| A/1 | 2 | 4 | 0 | 0 | 1 | 1 |
| A/2 | 2 | 6 | 1 | 1 | 1 | 2 |
| A/3 | 2 | 4 | 0 | 0 | 1 | 1 |
| A/4 | 2 | 6 | 1 | 1 | 1 | 2 |
| B/1 | 2 | 4 | 0 | 0 | 1 | 1 |
| B/2 | 2 | 6 | 1 | 1 | 1 | 2 |
| B/3 | 2 | 4 | 0 | 0 | 1 | 1 |
| B/4 | 2 | 6 | 1 | 1 | 1 | 2 |
| C/1 | 2 | 4 | 0 | 0 | 1 | 1 |
| C/2 | 2 | 6 | 1 | 1 | 1 | 2 |
| C/3 | 2 | 4 | 0 | 0 | 1 | 1 |
| C/4 | 2 | 6 | 1 | 1 | 1 | 2 |
| D/1 | 2 | 4 | 0 | 0 | 1 | 1 |
| D/2 | 2 | 6 | 1 | 1 | 1 | 2 |
| D/3 | 2 | 4 | 0 | 0 | 1 | 1 |
| D/4 | 2 | 6 | 1 | 1 | 1 | 2 |

## 验收边界

最终代码由独立克隆内的公开及隐藏 verifier 检查，同时要求 Agent 自己的成功验证对应最终工作区。D 组的退出码与新旧进程身份保留在 receipts。这里是生产 Engine/Context/SQLite thread 续跑，不是完整 TUI TaskRecord 生命周期验收。

v2 所有组接受相同改版；A/B 对比切窗，B/C 对比自然记忆和明确笔记要求，C/D 对比重启。四组均使用 persistent，不能据此宣称优于 summary。

完整证据：[结果 JSON](F:/code-ai-chaos/chaos-16-context-experiments/csv-continuity-v2-offline-host-02/results.json)；[冻结配置](F:/code-ai-chaos/chaos-16-context-experiments/csv-continuity-v2-offline-host-02/manifest.json)。

本报告使用脚本模型；usage 为脚本测试值，不能作为真实 API 费用或模型能力证据。
