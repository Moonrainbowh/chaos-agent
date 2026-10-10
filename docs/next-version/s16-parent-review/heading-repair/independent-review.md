# S16 标题覆盖与补全提示独立复核

结论：最终文件静态复核通过，未发现仍需修复的范围内问题。复核中发现的旧 v2 补全恢复问题已按最小方案修正。

范围：`src/code_agent/core/parent_review.py`、`_parent_review_validation.py`、恢复修复新增的 `_parent_review_repair.py`，及对应标题/提示/补全测试；另读取共享结束门以追踪恢复影响。未修改生产代码，未运行测试、全量套件或 Provider。

## 已确认的行为

- v2 的 `comparison_paragraphs` 把独立 ATX 标题原文放入下一正文组；尾部标题并入末正文组。含事实的标题没有从模型输入中消失。正文 ID 是必需覆盖对象，单列旧标题 ID 不能代替正文；旧标题 ID 仍可兼容接受（`_parent_review_validation.py:72,183-185`）。这是结构覆盖约束，不能据此证明模型确实正确讨论了每条标题事实。
- fence 状态跨空段保留，围栏中的 `#` 行仍是正文；错误长度或类型的闭围栏不会提前结束。CRLF 只在 v2 分组与 ID 检查中规范化；只有标题的报告保留所有原 ID。v1 继续原 `paragraphs` 和完整覆盖要求。相邻测试覆盖这些边界。
- `bundle` 只披露当前输出协议：正常阶段、不同阶段的遗留补全、v1 补全，以及坏 JSON/非对象的 v2 补全要求完整报告；可解析对象的 v2 字段补全要求 patch。保存原稿及具体错误仍进入同阶段 payload，独立阶段不暴露 child advisory（`parent_review.py:83-110`）。正常字段补全仍重算允许路径，完整改稿和正确字段修改仍拒绝。

## 发现并修正的恢复问题

旧 v2 comparison 快照可能仅因未覆盖独立标题而进入 repair，且已保存 `repair_used=True`。标题归组后原稿通过新完整校验，但旧代码仍提示 1..32 个 replacement；`_repair` 重算出的 allowed 集合为空，任何 replacement 或完整改稿均拒绝，原已有效稿无法完成。

最终修复对同阶段保存原稿重新 parse + inspect。只有完整重验无错误时，提示精确空补丁 `{"replacements":[]}`，并仅接受该对象后返回保存原稿；有效输出继续完整校验与 Host 引用绑定（`parent_review.py:61-64,99-105`；`_parent_review_repair.py:39-49`）。这不授予任意改稿、清空既有补全使用记录或新增补全机会。有任何现存错误时，空补丁仍拒绝，正常 1..32 路径限制保留。

`test_stale_heading_only_repair_accepts_only_explicit_empty_patch` 明确检查空补丁保留原稿、`repair_used` 不变、完整改稿/额外字段/非空补丁拒绝，以及仍有行号错误时空补丁拒绝。该测试与最小恢复方案对应，没有扩大状态机。

## 验证证据与边界

本复核只读取已有日志：`core-tests.log` 末尾为 `Ran 35 tests` / `OK`。根集成日志在本报告写入时尚未显示最终汇总，因此不在本报告内宣称其通过；由主 Agent 根据完成日志交付。未进行真实模型语义验收，也未将结构测试升级为来源分析质量或运行验证结论。
