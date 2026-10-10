# S16 与 main 集成

2026-10-10。将 S16 候选 `76abc59278b01d56ed0b842e462a5bd4dec70451` 与 main `acde09e3f6b5f0c676eeaedb1a5f44fbf029075f` 合并，30 个冲突文件已逐项解决。

main 使用较早的 squash 提交，Host、Core、Sessions、Context 的冲突来自后续 S16 增量。合并后 `src/` 和 `chaos_agent/` 与已验证的 S16 候选完全一致；main 独有的日常 CI 选测、手动兼容矩阵、Python 版本固定、runner 和 README 均保留。Python 扩展规范同时保留 S16 的相关回归入口和 main 的验证范围规则。

## 验证

Windows / Python 3.13.7，四组定向测试共 **238 项通过**。按用户明确要求，未运行全量测试、兼容矩阵或再次调用真实模型；提交和 GitHub 合并信息使用 `[skip ci]`，避免此次共享代码变更经 CI 选择器触发全量。

| 定向范围 | 通过 | 耗时 |
|---|---:|---:|
| CI selector and runner | 40 | 0.444s |
| Core and Sessions | 110 | 6.989s |
| Context and runtime | 26 | 11.165s |
| Host source and parent review | 62 | 220.998s |

冲突标记、unmerged index、`git diff --check` 均检查通过。独立复核核对了产品代码保留、main 新增 CI 功能及各组测试末尾结果。runner 测试日志内的零发现拒绝是预期反例，整组最终结果为 40 项通过。

结构化结果与本地日志 SHA256 见 [验证记录](main-integration-validation.json)。运行日志仅保留本机，不随提交发布。

## 验收边界

合并代码不等于 S16 严格全项通过。此前正式入口真实模型运行的父复核语义已通过，仍保留首次错误目标委派后重试的未通过项，见 [产品集成结果](product-integration/result.md)。本次没有改变原验收条件或将该项改判为通过。

主工作区的未提交认证改动及本地运行证据保持原位，本轮仅在隔离工作树内集成。
