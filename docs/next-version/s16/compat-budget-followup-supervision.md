# S16 旧预算回归修复独立复核

结论：**LIMITED_PASS**。此前 `compat-budget-supervision.md/json` 的 CHANGES_REQUESTED 和 13 个子场景失败物证保持原样。这份后续报告只放行 main 中三个根测试修复，并沿用此前候选 15 路径的限定兼容审查；不是当前候选完整套件、CI、GLM 或 S16 总体验收。

三个差异仅增加测试生命周期内 `CHAOS_MAX_PROMPT_TOKENS=20000` 的 `patch.dict` 与 `addCleanup` 恢复。所有原 20,000 断言、Skill 超限样本、零 HTTP/usage、记忆隔离、上下文冻结和嵌套 21,000 容量漂移反例均保持。这个设置明确保留原有较小预算回归；新授权默认的覆盖仍来自 `test_builtin_tool_prompt_budget.py`。

独立执行 main 对应三个模块：

- Python 3.10.20：10 测试，44.381 秒，退出 0；`compat-review-explicit-old-budget310.log/.exit`。
- Python 3.13.2：10 测试，21.952 秒，退出 0；`compat-review-explicit-old-budget313.log/.exit`。

实际比对 main 与候选 `4ed1a6a9d7ab315168ced21d14174638fea60e8c` 的原 15 路径，原始 SHA 全部相同；这轮没有产品修改。后续 JSON 记录三个 main 测试 SHA，以待同步的确切内容为审查边界；本报告不声称这三处已经提交、推送或通过新 CI。
