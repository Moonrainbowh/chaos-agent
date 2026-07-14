# Projects
以只读方式发现工作区已存在的项目生态和可用验证配方。

## 边界
- 负责：扫描 Python、Node 和 .NET manifest、已安装 executable 与不执行安装/restore 的验证候选。
- 负责：为候选提供来源、workspace-relative cwd、不可用原因及 trusted-workspace execution 风险说明。
- 不负责：安装依赖、联网、修改 lockfile、执行命令或擅自从多个项目中选择一个。
- 不负责：接收模型提供的完整命令、`npx`、package update 或 restore 参数。

## Units
- `ProjectKind`、`VerificationRecipe`、`ProjectCandidate`: 冻结生态发现和固定验证 argv | 无副作用 | candidate 必须包含 provenance 或 unavailable 原因
- `discover_projects(root, guard, executable_lookup)`: 有界只读扫描 manifest 和本机工具 | 读取 workspace manifest | 最多发现 16 个项目根，不执行命令
