# Semantic Insights
把共享 Unified Semantic Graph 转换为普通用户可直接调用、可解释且有界的代码认知报告。

## 边界
- 负责：消费同一 `RepoIndexSnapshot`，生成 Repo Map、Context Selection、Test Impact、测试优先级、Change Risk、Review Scope、Refactor Planning、Bug Localization 与 Dead Code 静态候选。
- 负责：所有结果携带 semantic generation、分析范围、依据与不确定性；路径和结果数量必须有界且稳定排序。
- 负责：先分析全部目标，再按 `limit/offset` 分页显示真实 total；单次变更范围超过 256 个文件拒绝分析而非截断；目录按路径边界匹配；循环依赖单列为无安全线性顺序的组。
- 负责：把动态派发、反射、插件入口和外部 API 无法静态证明的情况标为候选或限制，不得宣称确定死代码或确定根因。
- 不负责：创建第二套索引、读取任意工作区外文件、执行测试、修改代码、自动删除候选代码或绕过 Context/Verification 的 generation 契约。

## Units
- `InsightKind`、`InsightItem`、`InsightSection`、`SemanticInsightReport`：表达带 generation、分区、分数、置信类型和限制的不可变用户报告 | 无副作用 | `heuristic/candidate` 不得升级为 exact。
- `rank_context_nodes(...)`、`rank_bug_locations(...)`：按路径/符号命中和直接上下游关系生成稳定候选排序 | 无副作用 | 只使用共享图中静态事实，空命中返回空候选。
- `rank_impacted_tests(...)`：按反向依赖最短距离排列受影响测试 | 无副作用 | 约定匹配低于图可达测试，结果数量有界。
- `dead_code_candidates(...)`：寻找零静态消费者模块与无解析引用的私有符号 | 无副作用 | 仅输出 candidate，排除常见入口和测试文件。
- `SemanticInsightService.analyze(...)`：把 overview/context/impact/tests/risk/review/refactor/locate/dead-code 投影为统一报告 | 无副作用 | 输入路径必须存在于同代图；不执行测试、修改或删除。
- `repository_sections(...)`、`refactor_sections(...)`：提供目录/文件/符号/关系下钻以及全图影响排序与循环依赖组 | 无副作用 | 只有展示分页；不能把截断页当作完整影响范围。
