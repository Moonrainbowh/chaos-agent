# 检索与修复验证：三项机制改进

## 范围与改动

1. `search_text` 新增 `root`、`include_globs`、`max_results`。root 可指定目录或文件，枚举从该处开始，命中路径仍相对工作区。保留 2 秒默认 deadline；超时保留已有 matches 并返回 `complete=false`、`incomplete_reason=timeout`，即使零命中也不能解释为完整无结果。结果达到上限时保守标记 `result_limit`。扫描文件数量超限仍显式失败。
2. L0 对明确路径、函数名、独立名称、代码引用及复合标识符保留直接定位。普通类名单词不再全库直接占用 anchor；仅在候选文件中比较不同查询词覆盖、原有符号分数、touched 和文件排名。没有扩充停用词表或重调检索融合权重。
3. 任务提示要求预留验证预算、检查相邻既有行为，并如实报告验证缺口。VerificationPlanner 同时收集同名测试和 `test_<module>_*.py` 行为分组；隐藏验收保持独立。

## 搜索实测

同一 Windows 工作区、同一查询 `def _literal_columns`，每轮依次执行全仓库、Feature 目录、单文件，共三轮。未控制操作系统缓存；这是局部机制诊断，不是 Agent 效果评估。

| 范围 | 完整返回 | 耗时 |
|---|---:|---:|
| 全仓库 | 0/3 | 2.015–2.031 秒，均超时 |
| `src/code_agent/workspace` | 3/3 | 0.406–0.579 秒 |
| 指定 `_text_search.py` | 3/3 | 0–0.016 秒，计时精度有限 |

原始数据：[搜索实测](retrieval-improvements-20260910-search.json)。复现调用为 `WorkspaceFiles.search('def _literal_columns', root=scope)`，scope 分别为 None、上述 Feature 目录及完整相对文件路径。

## 验证边界

新增独立构造的 Session/cache 样本；明确引用可越过候选文件定位，普通词语的目标函数正文须实际出现在最终 system Prompt。另覆盖扫描目录剪枝、单文件范围、ignore/containment、扫描中途超时的部分命中、零命中超时、结果上限和参数校验，以及相邻测试分组。

这些是开发回归样本，不是新的盲测集。本轮未调用付费模型，不能据此声称原 24 次任务的通过率或 token 成本改善。

## 验证结果

- Windows 全量回归：27 套件，2565 项测试，23 项跳过，零失败，退出码 0。
- 全量流程的 Context 阶段先完成 182 项；随后补充最终正文断言并修正多词排序，最终 Context 单独复验 183 项全部通过。根集成套件运行于该最终逻辑上。
- sdist 与 wheel 构建成功；已读取 wheel 并核验四处实现路径。当前环境缺少可执行的 `build` 前端，使用项目声明的 `setuptools.build_meta.build_sdist/build_wheel` 直接构建，未安装依赖。
- `git diff --check` 通过。结果明细见 [验证摘要](retrieval-improvements-20260910-validation.json)。

本轮仅做本地实现与验证，未提交或推送。工作区原有的测试监管和连续性实验改动保留。

## 后续诊断修正

[放宽时间预算的试验](search-time-budget-diagnostic-20260910.md)确认：60 秒仍在搜索 Git 已忽略的 `.venv-regression` 安装环境。因此上面的范围缩小实测不能说明正确文件范围内的全仓库搜索本身很慢；应先对齐忽略规则，再确定预算和优化需求。
