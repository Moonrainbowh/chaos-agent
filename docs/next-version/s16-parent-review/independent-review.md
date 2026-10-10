# S16 父复核独立审查

- 日期：2026-10-08
- 审查者：独立子 agent，GPT-6.1-sol / medium
- 工作树：`C:\Users\Windows11\.codex\worktrees\s16-parent-review\chaos-16-agent`
- 范围：用户提供的父复核方案、Core/Host/Sessions 实现、授权、预算、恢复及交付门。
- 结论：本次审查发现的阻断问题已修复；当前未发现需要阻止本补丁集成的问题。该结论是编排与离线流程审查，不是实际模型语义质量通过。

## 独立运行的反例

使用工作树 Python 3.13.7 的 `.venv`，真实生产装配与 OpenAI Responses 请求序列化接口，传输层为 `httpx.MockTransport`；未调用真实 Provider，未执行被测来源中的测试或命令。

```powershell
.venv/Scripts/python.exe -m unittest tests.test_parent_review_boundaries -v
```

最终运行结果：**Ran 3 tests in 28.109s / OK**。

| 独立反例 | 实际检查 | 结果 |
|---|---|---|
| 首次父复核准备前已有新增用户输入 | 实际独立请求包含新增用户标记；不含子 advisory | PASS |
| 子委派闭合后、父来源准备前 Supervisor 暂停 | 保持 `paused / unverified`，有复核未交付 `remaining`；父仅 2 次请求、累计 3 次工具，未额外读来源 | PASS |
| 同一切点抛出 `EngineLimitError` | 同样保守暂停并返回 `remaining`，零额外来源读取与父复核请求 | PASS |

第二项最初独立复现失败：`TASK_PAUSED` 仅有 `task_id/status/reason`，没有 `remaining`。实现修正后，该单项复跑 **Ran 1 test in 8.138s / OK**。第三项单项运行 **Ran 1 test in 8.761s / OK**。最终三个反例已迁入 `tests/test_parent_review_boundaries.py`，模块仅发现这三项，不继承或重复发现生产者的测试。

## 已复核的修正

- 父复核绑定真实内置 `delegate_agent`、持久子线程关系及父任务授权；第二阶段取子线程最后完整答复，避免只比较已蒸馏摘要。
- 来源通过现有授权 dispatcher 读取，版本摘要和物理行保存在宿主快照；来源缺失、截断或容量不足不会正常交付。
- 独立请求绕过历史、notes、摘要及 project memory；比较阶段才加入同源正文、初判与完整 advisory。两个阶段沿用原 guarded model、持久 usage、工具/回合额度及取消边界。
- 独立初判缺项先在隔离状态补全；两阶段共享最多一次补全机会。正常和软租约退出经过共享交付门；硬额度及 Supervisor 暂停保留暂停语义并给出复核剩余项。
- 结构门校验任务/子任务目标、要求 ID、引用来源版本与行范围、advisory 段落覆盖及未知项。错误字段类型返回缺项，避免集合/字典查询因不可哈希值抛异常。
- 内部 JSON 输出不再作为普通用户正文及文本事件持久化；最终中文消息和 `delivered` 快照同事务写入，恢复到 `delivered` 无需再调用模型。
- 同一线程后续任务的 checkpoint CAS 使用真实尾记录；新增真实 user 消息通过持久游标吸收，完成前提升 followup，不因隔离历史而忽略新增用户要求。
- 每次持久记录都有容量检查，超限不截断来源制造成功，而保留明确交付失败。

以上授权、阶段装配、CAS、恢复与容量判断同时做了源码复核。该清单不表示每一项都由本审查者重新执行了完整测试；全量项目检查由主任务另行运行并记录。

## 证据边界

结构齐全仅证明可检查的流程事实，不证明语义判断正确。`completed / unverified` 继续保留；本独立审查者没有运行 GLM5.3-flash / medium 的真实 A/B 对照，不能据此宣布原 S16 的 `FAIL_PARENT_REVIEW` 或真实来源分析质量已经通过。主任务另行运行的局部对照见 [semantic-review.md](semantic-review.md)。也没有新增第二个被测审查子任务、扩大原任务授权或续加预算。

## 容量夹具追加独审

全量发现原有容量夹具问题后，同一独立审查子 agent 再次只读核对了 `tests/test_persistent_request_capacity.py`、诊断 helper、干净基线 archive 路径及实现者的实际执行记录，未自行复跑。结论：保留原两项测试 ID、8000 总额 / 7900 输入 cap、100 safety、真实 prepared preflight、零 HTTP 和历史不变；补充必需输入超限时 preflight 与 persistent assembly 双重拒绝。未削弱生产预算语义。

修后两项 1.546 秒通过的运行物证来自实现者；文档明确不声称完整生产 Host 提示可以容入 7900。首轮全量的原两项错误不因该修复而抹去。详见 [capacity-regression.md](capacity-regression.md)。
