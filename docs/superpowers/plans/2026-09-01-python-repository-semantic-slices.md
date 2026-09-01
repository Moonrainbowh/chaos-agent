# Python 仓库语义理解与分层上下文实施计划

> **给执行 Agent：** 本契约只允许一个最终提交，因此使用 superpowers:executing-plans，不使用逐任务提交。任务使用 `- [ ]` 跟踪。

**目标：** 在现有增量 Repo Index 上实现 scope-aware Python 直接语义图、请求派生测试影响、确定性 L0/L1/L2 上下文以及 stale-aware 批量切片读取，减少模型串行盲查。

**架构：** 数据严格分为 File Facts → Direct Semantic Graph → Request-derived Context。单文件 AST 扫描只生成事实，snapshot publication 只解析 `import/reference/call/inherits/config` 直接边，请求阶段才计算测试影响、Tier 选择和临时 L0 源码；所有路径使用 workspace-relative POSIX canonical form。

**技术栈：** Python 3.10+、标准库 `ast`、冻结 dataclass、内存 SQLite FTS5、`unittest`。

**执行 run ID：** `628b8b1e-b1ec-43fc-847c-3c98f56e3a84`

## 执行契约

**范围内：** Python scope/import/structured-reference 解析；直接语义图；配置 namespace/provenance；请求时反向测试影响；确定性 token packing；完整符号或 module-line L0；L1/L2；generation/FileSignature stale 校验；批量切片；Windows canonical/CRLF/encoding/reparse 边界；不可信仓库数据隔离；中文契约与旧路线修订。

**范围外：** 磁盘持久化、Tree-sitter/LSP 服务、第三方依赖、Embedding/向量库、运行时 tracing、完整动态派发、非 Python 语义对等、无界源码注入和交互式 L0-L4。

**固定决定：** 旧阶段 3 被本功能覆盖；Python 使用标准库 AST；`test_impact` 不是持久 relation；`import *` 和动态 `self` 派发不标 exact；L0 只发送完整符号或 bounded module-line slice；并发变化最多重建一次后 fail-closed；Repo Context 始终是不可信数据。

**验收：** 三层生命周期分离；scope/import/config 负例正确；相同 snapshot/query/budget 输出逐字节一致；stale/并发/Windows 边界失败闭合；批量工具避免 N+1 且允许后续新批次；完整测试通过或只记录经证明的无关既有失败。

**工作区与分支：** 在 `F:\code-ai-chaos\chaos-16-agent\chaos-agent` 当前 `main` 原地执行，起始 HEAD `bd2ef0c`，已领先 `origin/main` 两个提交。保留且绝不暂存预存 `uv.lock`。设计与计划原路径进入最终提交。

**提交策略：** 实现、审查、完整验证后只创建一个本地提交；只含批准范围路径。

**本地集成：** 当前 `main` 原地集成；不 merge、squash、rebase。

**基线失败策略：** 相关 Context 基线 23 项通过，已有 SQLite `ResourceWarning` 为无关。后续无关失败需隔离验证并报告，不未经确认修复。

**交付与清理：** 最终提交仅保留本地；不 push、不建 PR、不部署、不删除分支/worktree/规划文件，不清理用户文件。

**授权的外部或不可逆动作：** 一个精确范围的本地 Git 提交；其余无。

## 全局约束

- 只用 Python 标准库，不安装依赖。
- File Facts、Direct Graph、Request Context 类型和生命周期不得混合。
- 单文件最多 200 definitions、1,000 references/calls、100 config accesses、2,000 direct relations；签名 512 字符、Docstring 1,000 字符、骨架 32 KiB。
- 单轮最多 2 个 L0、8 个 L1、16 个 L2、2 hop；批量工具最多 16 targets、单 target 400 行、合计 128 KiB。
- L0/L1/L2 合计不超过现有 `repo_map_tokens`，L0 正文不进入 snapshot、持久状态、长期 cache、日志或度量。
- 内部路径统一为 workspace-relative POSIX canonical path；`uv.lock` 保持未修改、未暂存、未提交。

---

### 任务 1：实现 canonical path 与统一文本行边界

**文件：**
- 新建：`src/code_agent/context/repo_paths.py`
- 新建：`src/code_agent/context/tests/test_repo_paths.py`
- 修改：`src/code_agent/context/repo_scan.py`
- 修改：`src/code_agent/context/repo_index.py`

**结果：** Repo 内部键统一为 workspace-relative POSIX canonical path；Windows 大小写、CRLF/LF、encoding 和 reparse 越界具有稳定边界。

**实现：**
- 提供 `canonical_repo_path(path)`、`canonical_path_key(path)` 和统一逻辑行拆分函数；拒绝 absolute/drive/UNC/backslash/NUL/`.`/`..`。
- Windows key 使用 casefold，display path 保留受保护扫描拼写；重复 casing 合并为一个节点。
- scanner 与切片共用逻辑行函数；CRLF/LF 行号一致，未知编码沿用 Workspace 失败边界。
- junction/symlink 不另写字符串判断，继续依赖 Workspace guard/句柄身份，测试证明 canonical 化不能绕过它。

- [x] 按 TDD 实现
- [x] 运行：`python -m unittest src.code_agent.context.tests.test_repo_paths src.code_agent.context.tests.test_repo_scan -v`
      预期：POSIX canonical、casing、CRLF 和越界负例通过。

### 任务 2：提取 scope-aware Python File Facts

**文件：**
- 新建：`src/code_agent/context/repo_python_semantics.py`
- 修改：`src/code_agent/context/models.py`
- 修改：`src/code_agent/context/repo_scan.py`
- 修改：`src/code_agent/context/tests/test_repo_scan.py`
- 新建：`src/code_agent/context/tests/test_repo_python_semantics.py`

**结果：** 单文件 facts 包含定义范围/签名/Docstring、scope bindings、imports、references/calls、base/decorator/annotation 与配置 provenance，不作跨文件结论。

**实现：**
- 增加 `Symbol.end_line/signature/docstring`；定义 `PythonScope`、`ImportBinding`、`PythonUse`、`ConfigAccess`、`PythonFileFacts`。
- scope tree 覆盖 module/class/function/lambda/comprehension，记录参数、赋值、循环/with/exception target、import、global/nonlocal。
- `Name(Load)` 先解析最近 binding；局部/参数/shadowing 不产生外部引用。
- 提取 relative level、alias、star import、base、decorator、annotation 和简单字符串 forward reference。
- 配置 facts 记录 namespace/key/provenance/operation，不读取值；执行全部硬上限。

- [x] 按 TDD 实现
- [x] 运行：`python -m unittest src.code_agent.context.tests.test_repo_scan src.code_agent.context.tests.test_repo_python_semantics -v`
      预期：同名、参数、局部、comprehension、shadowing、global/nonlocal、structured reference 和 config facts 通过。

### 任务 3：发布 Direct Semantic Graph

**文件：**
- 新建：`src/code_agent/context/repo_semantic_graph.py`
- 修改：`src/code_agent/context/models.py`
- 修改：`src/code_agent/context/repo_snapshot.py`
- 修改：`src/code_agent/context/repo_index.py`
- 修改：`src/code_agent/context/tests/test_repo_index.py`
- 新建：`src/code_agent/context/tests/test_repo_semantic_graph.py`

**结果：** snapshot 只包含 `import/reference/call/inherits/config` 正向直接边及反向索引，不含 `test_impact`。

**实现：**
- `RepoEntry` 增加 `FileSignature` 和 relations；`RepoRelation` 固定五种 kind、exact/heuristic、config namespace/key/provenance。
- import roots 依次使用显式项目元数据、直属 `src/`、workspace root；歧义不标 exact。
- 完整支持 relative import、alias、显式 `__init__.py` re-export 与字面 `__all__`；star 只产生 module import 边。
- exact 仅用于唯一 scope/import/re-export/structured target；动态 self/cls、override、反射不冒充 exact。
- config exact 必须 namespace+key+兼容 provenance；裸 `timeout` 不串联。

- [x] 按 TDD 实现
- [x] 运行：`python -m unittest src.code_agent.context.tests.test_repo_semantic_graph src.code_agent.context.tests.test_repo_index -v`
      预期：relative/src/alias/re-export/star/inherits/decorator/annotation/config conflict 与增量 fixture 通过。

### 任务 4：实现请求派生测试影响与确定性 Tier packing

**文件：**
- 新建：`src/code_agent/context/repo_tiered_context.py`
- 新建：`src/code_agent/context/tests/test_repo_tiered_context.py`
- 修改：`src/code_agent/context/repo_ranking.py`
- 修改：`src/code_agent/context/repo_map.py`
- 修改：`src/code_agent/context/tests/test_repo_ranking.py`
- 修改：`src/code_agent/context/tests/test_repo_view.py`

**结果：** 每个请求从 Direct Graph 动态计算测试影响，并以逐字节可复现的算法选择 L0/L1/L2。

**实现：**
- 定义 `TaskAnchor`、`TierCandidate`、`TierSelection`、`TieredRepoContext`、`TieredRepoContextAssembler`。
- 请求时用 Direct Graph 反向索引在 2 hop/16 tests 内计算测试影响，不写回 snapshot。
- 实现设计文档固定排序键、原子块成本、60/25/15 初始 quota、两轮 first-fit、稳定回收/移除。
- 函数内 line anchor 选完整符号；module-level anchor 最多 80 行，绝不整文件。
- L0 最多 2、L1 8、L2 16；超大符号降 L1 并生成可拆分 targets。
- cache 只保存不含正文的 TierSelection，key 包含 tokenizer version。

- [x] 按 TDD 实现
- [x] 运行：`python -m unittest src.code_agent.context.tests.test_repo_tiered_context src.code_agent.context.tests.test_repo_ranking src.code_agent.context.tests.test_repo_view src.code_agent.context.tests.test_repo_map -v`
      预期：动态 test impact、module-level 负例、确定性 packing、预算和 cache 通过。

### 任务 5：接入 L0 临时读取、并发 fail-closed 与不可信数据隔离

**文件：**
- 修改：`src/code_agent/context/builder.py`
- 修改：`src/code_agent/context/_builder_support.py`
- 修改：`src/code_agent/context/repo_map.py`
- 修改：`src/code_agent/context/tests/test_builder.py`
- 修改：`src/code_agent/context/tests/test_repo_view.py`

**结果：** Provider 首轮获得签名一致的 L0/L1/L2；仓库数据被结构化标为不可信，并发二次变化时不发送跨代 Context。

**实现：**
- 读取前后校验 generation/FileSignature；stale 精确失效并最多重建一次，再变化则本轮完全省略 Repo Context。
- L0 正文不进入 cache；完整符号或 bounded line slice 原子渲染。
- 固定可信说明与 JSON-compatible escaped `UNTRUSTED_REPOSITORY_DATA` 分离，comment/Docstring/伪 delimiter 只作为数据。
- 保持 greeting/no-project/disabled 的零装配；不改变 prompt 总预算顺序。

- [x] 按 TDD 实现
- [x] 运行：`python -m unittest discover -s src/code_agent/context/tests -v`
      预期：stale range、读取中修改、第二次变化、prompt injection、预算和 Context 回归通过。
- [x] 运行：`python -m unittest discover -s src/code_agent/core/tests -v`
      预期：Core Context 契约通过。

### 任务 6：实现 generation-aware 批量代码切片 Feature

**文件：**
- 修改：`src/code_agent/workspace/files.py`
- 修改：`src/code_agent/workspace/AGENTS.md`
- 新建：`src/code_agent/workspace/tests/test_batch_code_slices.py`
- 修改：`src/code_agent/core/task_state.py`
- 修改：`src/code_agent/core/tests/test_task_state.py`

**结果：** Workspace 可原子读取 1–16 个带 expected signature 的 canonical ranges；超大符号可拆分；成功路径进入 files_read。

**实现：**
- 定义 `CodeSliceRequest/CodeSlice`，包含 generation、canonical path、range、expected size/mtime/device/file identity。
- 读取前完整校验，读取后复核；任一 stale/path/range/encoding/reparse 失败则整批无源码结果。
- 单 target 400 行、合计 128 KiB；允许同文件连续不重叠 targets，拒绝重复/冲突范围。
- logical lines 统一处理 CRLF；返回 encoding/newline 元数据。

- [x] 按 TDD 实现
- [x] 运行：`python -m unittest src.code_agent.workspace.tests.test_batch_code_slices src.code_agent.core.tests.test_task_state -v`
      预期：拆分、stale、casing、CRLF、encoding、junction/symlink 和 files_read 通过。

### 任务 7：完成 Windows typed-tool、模式与 policy 集成

**文件：**
- 修改：`code_agent_win/tool_schema.py`
- 修改：`code_agent_win/tools.py`
- 修改：`code_agent_win/workspace_actions.py`
- 修改：`code_agent_win/agent_modes.py`
- 修改：`code_agent_win/subagents.py`
- 修改：`code_agent_win/runtime_support.py`
- 修改：`code_agent_win/AGENTS.md`
- 修改：`src/code_agent/policy/classifier.py`
- 修改：`src/code_agent/policy/tests/test_classifier.py`
- 修改：`tests/test_tool_schemas.py`
- 新建：`tests/test_batch_code_slice_tool.py`

**结果：** `read_code_slices` 成为严格只读工具，支持 generation/signatures 和多 targets；提示鼓励批量而非绝对只许一次。

**实现：**
- strict schema：generation + 1..16 targets，每项 path/start/end/expected size/mtime/device/file identity。
- 注册到标准 READ_TOOLS、只读子 Agent 白名单、runtime risk 和中央 policy。
- dispatcher 使用当前 RepoIndex generation 验证请求并调用 Workspace Feature；只读、不失效 dirty。
- 工具描述要求合并本轮已知目标，允许新信息后的后续批次。

- [x] 按 TDD 实现
- [x] 运行相关子集：`python -m unittest tests.test_tool_schemas.ToolSchemaTests tests.test_batch_code_slice_tool src.code_agent.policy.tests.test_classifier -v`
      结果：schema、调度、pending invalidation stale、policy 通过；`tests.test_tool_schemas` 全类仍有 3 个与本功能无关的既有 Windows 编码/原子写入失败，单独记录而不冒充通过。
- [x] 运行：`python -m unittest tests.test_command_integration tests.test_agent_app_construction -v`
      预期：Application 组合不回归。

### 任务 8：更新中文契约与旧阶段路线

**文件：**
- 修改：`src/code_agent/context/AGENTS.md`
- 修改：`src/code_agent/workspace/AGENTS.md`
- 修改：`code_agent_win/AGENTS.md`
- 修改：`docs/research/incremental-repo-map-design.md`
- 修改：本设计与计划文档（仅机械同步最终接口名）

**结果：** 文档与实际三层模型、Tier、安全边界一致，旧阶段 3 改为 Python Tiered Context，持久化仅为延期项。

- [x] 更新契约并删除旧的“持久化阶段”和 L0–L4 路线
- [x] 运行：`rg -n "阶段 3|持久化|File Facts|Direct Semantic Graph|Request-derived Context|L0|read_code_slices|UNTRUSTED" docs/research/incremental-repo-map-design.md src/code_agent/context/AGENTS.md src/code_agent/workspace/AGENTS.md code_agent_win/AGENTS.md`
      预期：所有边界与实现一致。

### 任务 9：完整回归、审查与唯一最终提交

**文件：** 审查任务 1–8 全部变更；保留 `uv.lock`。

**结果：** 范围内变更完成审查、完整验证并进入唯一一个本地提交。

- [x] 尝试全部源码测试目录；除 `attachments`、`sessions`、`workspace` 的既有 Windows 临时文件身份/原子替换失败外，其余目录通过。范围内 Context 全集最终为 137 项通过，Core 全集 107 项通过。
- [x] 尝试根集成；全量运行超过 3 分钟无终态后人工终止。范围内 schema/batch/policy/command/application 组合子集通过；既有 `tests.test_tool_schemas` 3 项 Windows 编码/写入失败保留为基线例外。
- [x] 执行 Superpowers code review 与 verification-before-completion；三轮复核发现的 Critical/Important 已全部修复，最终门禁无 Critical/Important。
- [x] 运行 `git diff --check`，核对 exact paths 和 `uv.lock` 未触碰。
- [x] 使用 git-commit 流程只暂存批准路径，创建唯一中文本地提交；明确排除预先存在的 `uv.lock`。
- [x] 在最终交付记录 commit ID、测试数量、symlink 权限跳过和经证明的无关既有 Windows 基线失败。
