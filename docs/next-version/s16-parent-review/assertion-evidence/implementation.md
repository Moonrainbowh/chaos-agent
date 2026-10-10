# 断言见证修复与定向验证

本轮仅在既有父复核路径上增加 v3 证据记录，未改真实评测来源、答案、模型、预算、回合数或 verification 含义。工作树 `codex/s16-parent-review`、HEAD `b28868a92f22106688c433c698ccbc6af982c892` 加当前未提交候选；未提交、推送或合并。

## 实现

- Core 新增 `_parent_review_assertions.py`：绑定既有物理行引用，检查普通 JSON 字面值及正反见证一致性，Host 重算当前断言结果；输出范围限定为具体见证。无法表达的观测/见证必须保留 unknown。
- `parent_review.py` 为新协议加入记录格式和简体中文交付约束；正常输出与字段补全仍只披露一种接受格式。renderer 展示原始字面值、被拒绝/可通过的具体见证和契约反例，不把有限例子渲染成全称保证。
- `_parent_review_validation.py` 按版本接入新检查；旧 v1/v2 无新字段要求。Host 只把新快照版本切至 3，不迁移旧任务、不改变独立→比较阶段、唯一补全和预算。
- 同步 Core/Host 受影响契约与两个集成 fixture。结构 fixture 的空断言表显式列出未知，不能作为语义通过样例。

## 验证

先失败再修复：交付4项红测实际为3 failures/1 error，直接复现旧门接受错误通过见证、遗漏见证仍交付、没有Host计算结果。新增断言helper的逻辑stub红测12项22 failures/5 errors；首次仅缺模块的引导失败不作为逻辑辨别证据。独审发现未知字段 JSON pointer 缺少转义，新增补全回归在 `a/b`、`a~b`、`a~/b` 三种字段名下先失败，再用标准转义修复。

最终定向结果：

- [Core最终日志](core-final-tests.log)：**54项 / 0.011s / OK**。此前53项通过日志保留在 [core-tests.log](core-tests.log)，最终新增一项pointer补全回归。
- [三个相关集成模块](integration-tests.log)：**46项 / 207.876s / OK**，真实Host/Provider序列化的离线注入测试，零付费模型调用。
- [独立代码审查](code-review.md)：GPT-6.1-sol / medium，仅只读审查及报告写入；pointer问题已修，暂无剩余阻塞。没有自行执行或冒称复跑测试。

命令：

```text
.venv/Scripts/python.exe -m unittest src.code_agent.core.tests.test_parent_review_contract src.code_agent.core.tests.test_parent_review_repair src.code_agent.core.tests.test_parent_review_prompt_modes src.code_agent.core.tests.test_parent_review_headings src.code_agent.core.tests.test_parent_review_assertions src.code_agent.core.tests.test_parent_review_evidence_delivery
.venv/Scripts/python.exe -m unittest tests.test_parent_source_review tests.test_s16_source_completion tests.test_source_completion_contract
```

没有运行整仓或跨平台套件。字面值比较不能证明模型的静态推演、fault描述或自由文本结论正确；真实模型验收结果单独记录，不由本页测试成功推出。
