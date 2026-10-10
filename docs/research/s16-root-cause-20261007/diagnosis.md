# S16 子任务提前结束：根因诊断

日期：2026-10-07。审查对象：对话 `01a104d3-f6f3-74f3-9dcd-901f4036a4e5`、v7 原始请求和事件、冻结候选 `afbd8f3ee7163cb71959a6af0bdfb77b6c082cdd`。本次未修改生产源码、未调用 Provider、未启动 v8。候选 tracked 工作区干净。

## 结论

“模型没有调用读取工具就返回”准确描述触发点，但不是完整根因。当前链路存在三个不同层面的问题：任务结束协议没有约束来源工作完成；子任务专用指令在组装中丢失，而父任务流程进入子上下文；只读父任务被确定性误分类为修改任务。模型自身还给出了错误的行为分析，不能将它归结为预算不足，也不能用框架问题替模型错误开脱。

从第一性原理看，任务至少需要依次满足：能力可用、目标明确、执行取证、分析交付、按目标判断完成。现在工具可用已成立，但“模型本轮不再发工具”能直接终止子运行；“四来源读取及分析完成”仅在事后独立审查时检查，没有成为子任务运行中的完成条件。最终审查能拒绝误报，不能自动让子任务继续工作。

## 已确认的证据

### 1. 结束条件与任务目标分离，缺少来源完成检查

候选 `src/code_agent/core/_engine_turn.py:118` 在没有工具调用时进入 `_finish_without_calls`；其第 238–250 行在没有 TaskRecord 的分支直接发布 COMPLETED 并停止。`chaos_agent/child_runner.py:90` 用 objective 启动子引擎，没有传入子 TaskRecord。子预算与父授权存在，但不等于子任务具有来源完成条件。

这并不是 verification 被伪造：结果仍是 `verification_status=unknown`，`completed` 只表示运行结束。但对于“实际读完并分析”的需求，运行器不会因缺少四份读取而继续或报告明确缺项。父任务可以识别建议不完整，却只能在本次样例中自行补读；这不能补成子任务验收成功。

`replay.py` 将 v7 真实 ModelEvent 流送入候选真实 AgentEngine 和 EngineChildRunner，使用内存会话及工具/context 替身：

| 流 | 模型轮数 | 披露/读取 | 子运行状态 | 验证状态 | 来源要求 |
|---|---:|---|---|---|---|
| 原始 v7 事件回放 | 2 | 1 / 0 | completed | unknown | FAIL |
| 第二轮改为四次读取的合成对照 | 3 | 1 / 4 | completed | unknown | PASS |

两次执行得到相同失败判据。命令附 `--require-source-completion` 时实际 exit 1，断言为 `RED: child ended before required source reads`。对照只证明相同引擎能继续执行工具，不代表真实模型能给出正确分析，更不代表 S16 通过。

### 2. 实际提示与预期的子任务提示不一致

- `wire-request-3.json` 和 `wire-request-4.json` 均已有完整的 `load_tool_contract`、`read`、`search` schema。`read` 第一轮就可用，样例仍强制“先披露，下轮读”，增加了多余步骤。
- 第二轮历史工具输出仍带 `availability=next_model_turn`；模型回答正是“已披露，下一轮生效……下一轮将读取”。模型是否因这个措辞产生时间指代误解尚不能用离线回放证明，但它是有直接文本对应的强假设。
- 插件 manifest 中 `instructions` 以 `Read the authorized sources only.` 开头，该专用指令不在真实子请求中。`RuntimeDispatcherFactory.child_engine` 只把 `agent.mode` 等传入 `for_child`，没有把 `agent.instructions` 传给上下文；`EngineChildRunner` 只传 `request.objective`。这是确定的指令传递缺口，不能认为修改插件 instructions 就已经修改了模型所见提示。
- 同一子请求的 system 却包含完整 AGENTS 验收流程，包括“先委派一次”“自己检查 child's advisory”等父任务职责；子 user objective 又禁止进一步委派。角色和流程没有清楚分离。
- repository map 已预注入两份 contract/legacy 文档的正文。因此准确表述是“子任务没有执行四份文件的 read，缺少完整四来源覆盖”，不能扩大为“上下文没有任何来源内容”。

这些事实证明确有提示组装问题；它们各自对模型输出的因果贡献还需要固定模型和输入的单变量实验，不能直接宣称是某一句话唯一导致失败。

### 3. 只读任务被识别成修改任务，形成独立阻塞

v7 worker 原始父 TaskContract 的 intent 是 `modify`；最终 SYSTEM 为 `waiting_decision`，理由是没有工作区改动。实际用户目标与权限都是只读调查。

候选 `src/code_agent/core/task_intent.py:44` 识别 `read only` 和 `readonly`，遗漏 `read-only`；后续对 write/test 等词作子串匹配，最终默认 MODIFY。用同一个真实父 prompt 调用同一个生产函数：

- 原始 `read-only`：`modify`。
- 仅替换成 `read only`：`analyze`。

`engine_completion.py:15–27` 因该错误 intent 要求文件发生修改。即使本次父子分析文字全部正确，父任务仍会走这个不适用的“无修改等待”分支。这与子任务为何提前结束是两个独立问题。

### 4. 分析错误另有证据，不能混入来源获取问题

父最终实际请求包含四份完整工具结果，仍把 `return list(values)` 概括为五约束中仅“不修改输入”成立。实际它还保持输入顺序和重复元素；缺失的是 trim 和过滤空白。测试重复项同时测试 trim，不能把该用例失败归因为“不保留重复”。测试定义行号也有偏移。

完整来源进入请求只能证明输入覆盖，不能证明正确理解。这部分仍是该真实样本的模型分析质量失败；本次没有评估模型总体能力或 high 的改善概率。

## 为什么此前不断换一种错误

| 样例 | 实际失败机制 | 能否解释 v7 |
|---|---|---|
| v5 | 4 次工具额度容不下披露 1 次加读取 4 次 | 不能，v7 已给 5 次 |
| v6 | 完成四次读取后，第三轮回答在子 90 秒期限取消 | 不能，v7 子 240 秒且无该取消 |
| v7 | 只披露就返回计划、结束协议不检查来源；父分析错误及 intent 错配 | 当前定位 |

此前解决了资源可行性问题，但没有先锁定“读完、答对、按只读目标结束”的最小闭环。不断改变预算和时限，只会让下一个缺口暴露。v8 同时改变 effort、子 base_mode/prompt policy 和任务措辞，即使通过也无法单独归因于 high。

## 建议的最小处理顺序（本次未实施）

1. 修正只读 intent 识别及子 agent.instructions 的实际传递，用真实公开组装入口的离线测试确认冻结 intent 和发送正文。保留已有权限和父子预算语义。
2. 简化验收输入：父、子职责分清；已完整提供的读取工具直接使用；避免“下一轮生效”被解释成等待用户下一轮。验收运行前检查真实请求，而不只检查 manifest 或提示模板。
3. 给明确的来源调查目标建立最小完成检查：必要来源的实际可用证据、已交付分析或明确缺项。缺项时在原预算内反馈并有界续轮，无法完成就准确返回未完成。不能将通用自然语言判断或无限重试冒充确定性验收，也不能将建议升级为 verified。
4. 在同一 medium、同一源码和验收条件下做一次有授权的对照复验，分别检查来源覆盖、行为结论、引用正确性和只读终态。之后再单独比较 high，避免同时改多个变量。

## 可复现命令与物证

```powershell
& 'C:/Users/Windows11/.codex/worktrees/next-version-s2-baseline/chaos-16-agent/.venv/Scripts/python.exe' -X utf8 'F:/code-ai-chaos/chaos-16-agent/docs/research/s16-root-cause-20261007/replay.py' --require-source-completion
```

当前期望 exit 1，表示能捕获仍未修复的失败；不是本次诊断脚本意外崩溃。

- 本次：`replay.py`、`replay-result.json`、`replay.log`。
- 原始记录：`docs/next-version/s16/owned-cases/next-version-s16-investigation-v7-09b160648af4/` 下 child JSON、wire-request-3/4/6、worker-result。
- 原独审：`docs/next-version/s16/v7-actual-independent-supervision.md`、`v7-wire-source-independent-supervision.json`、`v7-order-duplicates-counterexample.json`。
- 原先候选：`docs/next-version/s16/investigation-v8-preflight.md`。

本报告补充根因诊断，不改写已有失败、pending usage、验收结论或冻结候选。
