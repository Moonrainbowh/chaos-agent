# Workspace
提供受工作区边界约束的代码发现、读取、搜索、补丁、Diff 与版本控制操作。

## 边界
- 负责：路径规范化与 containment、忽略规则、文件读取、目录浏览、文本搜索和原子补丁。
- 负责：生成可审阅 Diff，并在 Git 仓库中提供 checkpoint 与 revert 所需的工作区能力。
- 负责：仅通过 Host 注入且经策略明确授权的仓库外 artifact storage 保存 snapshots，并恢复回合前 bytes，包括 dirty baseline；不接受任意外部路径。
- 负责：以无副作用能力探测区分 Git 仓库与普通目录，供集成层决定是否暴露 Git 工具。
- 不负责：执行任意 shell 命令、调用模型、决定是否批准动作或渲染 Diff。
- 不负责：未获上层策略授权的工作区之外路径、符号链接/reparse 目标或敏感文件。
- 不负责：访问默认本地 API 配置目录及其内容，即使该目录被选作工作区。
- 不负责：在普通目录中伪造 Git 状态、自动初始化仓库或决定上层是否注册 Git 工具。
- 依赖：Chaos Agent 和旧 code-agent 的本地配置目录均视为敏感路径。
- 负责为成功 typed 写入记录 `ActionEffect` 所需的改动路径与内容 hash，并在有界扫描内计算当前 subject snapshot/hash；不判断业务正确性。

## Units
- `SubjectSnapshot`、`snapshot_subject(...)`: 为改动文件和关键 manifest 生成有界、确定性的 subject hash | 读取受 guard 保护的工作区文件 | 不判断业务正确性或扫描工作区外路径
- `WorkspaceError` 及其专用子类：表达路径、敏感文件、文本类型、大小、扫描上限、超时与编辑冲突 | 无副作用
- `WorkspacePathGuard(root).resolve(path): Path`：规范化路径并执行 containment 与敏感路径策略 | 检查路径元数据 | `allow_outside` 仅供已批准 dispatcher 使用；链接/reparse 组件及任意层级 `.git`、`.chaos-agent`、`.code-agent` 始终受保护
- `IgnoreRules.from_workspace(root): IgnoreRules`：加载内置忽略项和根 `.gitignore` 的常用规则子集 | 读取根 `.gitignore` | 支持顺序反选，不是完整 Git parser
- `WorkspaceFiles.list_files(root, ...): tuple[str, ...]`：在有限扫描预算内按全局路径顺序枚举可访问的非忽略文件；显式外部 `root` 可递归枚举 | 扫描超限显式失败，不返回伪完整结果
- `WorkspaceFiles.read_text(...): TextDocument`：按包含式行范围读取 UTF-8/UTF-8 BOM 文本 | 读取单个文件 | 拒绝二进制与超限文件
- `WorkspaceFiles.search(...): tuple[SearchMatch, ...]`：在有限扫描预算和全局 deadline 内执行 literal/regex 文本搜索 | deadline 覆盖目录枚举与文件匹配 | 超时不返回部分结果
- `WorkspaceEditor`: 有界读取现有文件，生成写入/单次替换 Diff 并校验哈希后原子应用 | 单文件同目录临时写入与替换
- `WorkspaceSnapshot`: 复制受保护路径的原始字节并恢复创建、更新与删除 | 多文件逐项原子恢复 | 不承诺多文件事务原子性
- `GitDiffSnapshot`、`GitWorkspace`：提供有界的仓库检测、porcelain 状态、单命令 unstaged Diff 与 staged/unstaged/untracked 不可变 facets | 仅执行固定 Git argv，并在同一累计字节预算内全局预检 tracked 路径、按已验证路径生成严格 UTF-8 patch、从已核验真实句柄读取 untracked 文件 | 每个 Git 路径和打开句柄均重新经过 guard；ignored 文件排除，二进制只输出稳定元数据 marker，超限或超时终止且不提供任意 Git 命令入口

## 环境依赖
- 运行：Python 3.10+ 与 PyPI `regex`（为用户正则提供单次匹配 timeout）。
- 测试：`uv run --with regex python -m unittest discover -s src/code_agent/workspace/tests -v`
