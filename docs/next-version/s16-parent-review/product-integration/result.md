# S16 产品集成结果

2026-10-10。**父复核产品路由、Workflow 长标题和混合模型费用显示已完成集成；本次真实父复核语义通过。严格 S16 整链仍不能全项放行：普通主模型多发了一次选错目标的委派。**

候选位于隔离工作树 `C:\Users\Windows11\.codex\worktrees\s16-parent-review\chaos-16-agent`，分支 `codex/s16-parent-review`，HEAD `b28868a92f22106688c433c698ccbc6af982c892` 加冻结的未提交改动。本轮未合并主工作区，也未改全局默认配置。

## 已集成的行为

- 正常 `create_application` 读取 `[agent].parent_review_profile`。父独立复核、比较与原有一次补全可使用独立 profile；普通父轮与 child 保留原 profile。本次使用普通/child `glm-5.3-flash`，父复核 `global:gpt-6-astra`，medium。
- 新快照首次持久化即冻结模型身份；恢复漂移或缺失在模型发送前拒绝。旧无绑定快照维持原行为。MODEL_STARTED、实际发送与 usage 共用同一请求选择，继续使用同一任务 ledger 及原预算。
- 额外 provider 懒加载，随正常运行时切换、构造失败和关闭清理；没有把实验适配器或实例 monkeypatch 接入产品。
- Workflow 仅将超长显示标题缩为最多512字符，完整 objective 保留。此前真实545字符子目标可正常投影；本轮真实 stderr 为0字节。
- 多模型任务保留全部实际token，显示实际模型集合，缺少逐模型价格归因时费用为未知，避免按普通模型费率误算。

## 本次真实证据

案例：[2874dc205ca6 运行审计](attempts/s16-source-completion-p4-2874dc205ca6/runtime-audit.json)。独立保存原始 HTTP 请求、SSE、持久消息、快照、事件、来源与代码哈希。

| 检查范围 | 结果 |
|---|---|
| 正式入口、模型分路、隔离、持久身份、用量、交付、stderr | 20项全部通过 |
| 父独立分析、比较与具体见证 | PASS；初判正确，最终纠正子报告错误 |
| 子报告原子主张 | 27项均有实质复核，含排序/去重错误与内部矛盾 |
| 一次指定代理的成功子任务 | 1个真实绑定子线程，PASS |
| 严格只尝试一次委派 | FAIL；两次调用，第一次目标错误被拒 |

实际7次响应，171.130秒，42866 tokens全部结算：普通/child GLM 24293，父复核 Astra 18573；child 7931已计入总额。共享7轮、11工具、零续额，仍为12轮/40工具/1000000-token硬上限。独立为request6、比较为request7，均无结构错误，`repair_used=false`。来源和候选无漂移。

最终生产结果 `completed / unchanged / unverified`。没有执行被审查源码、测试或错误实现；静态见证通过独立推导，不冒充执行验证。[最终模型正文](attempts/s16-source-completion-p4-2874dc205ca6/delivered-review.md)、[独立语义复核](attempts/s16-source-completion-p4-2874dc205ca6/independent-review.md)、[逐项结论](attempts/s16-source-completion-p4-2874dc205ca6/checks-review.json)。

## 未放行项与根因

首次普通 GLM 主轮将指定 `s16.sourceaudit` 错误地写为 `role="subagent"`，没有提供 `agent_id`。原冻结验收入口拒绝了这一目标，错误保留为 `AssertionError`，没有创建子线程。模型随后改为 `agent_id="s16.sourceaudit"` 才成功。

因此7次请求属于普通父3、child2、父复核2，不是一次隐藏补全。审核脚本按快照的 delegate ID 找到实际子线程，同时保留失败观测；没有把 children 首条空线程当作真实 child，也没有删除失败记录来制造通过。

剩余点在**普通主轮遵守指定代理选择与一次尝试约束**。本次父复核正确保留了无法确认全过程的未知项；该问题不归因于复核模型、排序见证或标题修复。未扩大预算、改写原任务或重复抽样追求全绿。当前总验收状态为 `BLOCKED_STRICT_TASK_PROCESS`，代码集成与来源语义通过须分别报告。

## 定向验证

[验证记录](validation.json)：Core父复核72、Engine50、Config35、Workflow12、费用/用量10、新正式入口5、原父复核与来源回归46、运行时选择4、部分构造清理3项通过。仅相关定向检查，没有全量测试。开发过程中的错误测试模块名、异步关闭断言修正及既有配置限制均在记录中保留。

[代码独立复核](code-review.md)未留已知产品代码阻断。`git diff --check` 与真实运行后的候选冻结检查通过。此前实验 PASS 和失败证据保持原样，不替代这次正式入口的验收边界。
