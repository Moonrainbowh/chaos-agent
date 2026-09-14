# 搜索范围与预算：复用 Git 清单，默认 10 秒

## 处理结果

Git 工作区的 `search_text` 现在复用已有的 `GitWorkspace.snapshot_paths`，候选包含 tracked 与非 ignored untracked 文件。子目录 `.gitignore`、反选及 Git 自身的排除规则由 Git 处理；不再把被 Git 忽略的 `.venv-regression` 安装环境当作项目搜索范围。

Workspace 对候选继续做 containment、敏感路径、目录或单文件范围及可见性检查；Git 枚举也消耗同一个全局搜索 deadline。枚举失败不回退为扩大扫描，Git 超时转换为明确的不完整结果。普通非 Git 目录仍采用原有受保护遍历和根忽略规则，此次没有新增通用 Git ignore 解析器。

修正范围后，2 秒预算仍不足，5 秒接近初测的完整耗时上界。因此默认预算从 2 秒改为 10 秒；保留目录/glob 范围、结果上限以及超时部分结果，暂不追加其他搜索算法优化。

## 耗时证据

查询与目录范围沿用前次诊断：`def _literal_columns`、工作区根目录。

- 修正前：放宽到 60 秒仍未完成，尝试读取 9414 个文件，最后仍在安装环境中。
- 修正后初测：2 秒 0/3 完整；5 秒与 10 秒均 3/3 完整，约 3.8–5.0 秒，读取 1371 个文件，安装环境读取数为 0。初测部分时段与 Workspace 测试重叠，不作为精确性能基准。
- 测试结束后，通过真实 `RootActionDispatcher` 再测三次：默认时间预算 10 秒，完整耗时 **3.451–4.056 秒**，全部找到 `src/code_agent/workspace/_text_search.py`，无安装副本命中。

最后三次为测完整扫描，显式将 `max_results` 设为 1000。新增诊断文件包含重复查询文本，使该查询共有 218 条命中；默认 100 条会先触及 `result_limit`，这与时间超时是不同边界。产品默认结果上限没有更改。最终摘要不复制命中正文，避免继续放大重复文本。

[初测原始记录](search-git-inventory-20260910.json) · [最终工具调用摘要](search-git-inventory-default-20260910.json) · [修正前预算诊断](search-time-budget-diagnostic-20260910.md)

## 验证

- 最终 Workspace 套件：467 项，21 项平台相关跳过，零失败。
- 工具搜索集成 5 项、工具 schema/dispatcher 8 项，全部通过。
- 包含真实 Git 子目录忽略与反选、单文件范围、候选去重、敏感与越界拒绝、枚举失败不回退、共享 deadline、Git 超时归一化和结果上限测试。
- wheel 构建成功，已读取包确认包含新 inventory Unit 和 10 秒默认配置；`git diff --check` 通过。
- 本轮是相应 Feature 和工具集成验证，没有重新运行所有 Feature 或付费模型评估。
