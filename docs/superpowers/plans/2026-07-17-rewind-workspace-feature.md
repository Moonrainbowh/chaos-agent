# Workspace 确定性回溯状态实现计划

> 执行方式：按任务顺序使用测试驱动开发；每个实现任务完成后分别做规格审查与代码质量审查。

**目标：** 让 Workspace Feature 提供可信回溯所需的确定性文件事实，并让上层稳定地区分快照“缺失”和“存在但损坏”。

**边界：** 本计划只修改 `src/code_agent/workspace/`。它不写 Sessions journal，不决定 mutation/checkpoint 顺序，不做 action 归因，也不判断 checkpoint 是否最终可回溯。

**结构：** 新增独立的 `rewind_state.py`，从 `EditPlan` 和受保护的原始工作区 bytes 生成不可变状态。快照加载继续由 `WorkspaceSnapshotStore` 统一入口负责，但把已接近 300 行的 manifest 编解码/验证逻辑拆到私有模块；`SnapshotHandle` 仍能从 `code_agent.workspace.snapshot_store` 导入。

**约束：**

- 所有源码和测试文件不超过 300 行，函数不超过 50 行。
- 读取、路径规范化和预算均复用 `WorkspaceEditor`/`WorkspacePathGuard` 的现有安全边界。
- 状态观察不写文件、不调用 `WorkspaceEditor.apply()`，超出预算时不返回部分结果。
- load 的缺失/损坏分类必须失败闭合；权限错误、链接/reparse、路径身份变化不得伪装成缺失。
- 每个任务只暂存自己列出的 Workspace Feature 文件。

---

## Task 0：先修复 Workspace 既有 Python 粒度违规

**Files:**

- Modify: `src/code_agent/workspace/files.py`
- Create: `src/code_agent/workspace/_text_search.py`
- Modify: `src/code_agent/workspace/_git_process.py`

当前 `files.py` 为 310 行，`WorkspaceFiles.search()` 为 72 行，已违反
`AGENTS.python.md`；`_git_process.py` 的 `collect_bounded_output()` 也有
92 行。这是本 Feature 最终验收的前置修复，不改变公开行为。

### Step 1：冻结现有搜索行为

先运行现有 characterization tests：

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest src.code_agent.workspace.tests.test_files -v
& $python -m unittest src.code_agent.workspace.tests.test_git_limits -v
```

预期：通过。若失败，先记录基线问题，不得在机械拆分中悄悄改变语义。

### Step 2：机械拆分搜索单元

把 `SearchMatch`、搜索参数校验、regex 编译、deadline、include glob、
literal columns 和有界匹配循环移动到 `_text_search.py`：

- `files.py` 继续 re-export `SearchMatch`，保持
  `from code_agent.workspace.files import SearchMatch` 兼容。
- `WorkspaceFiles.search(...)` 保留相同签名，只把已绑定的
  `_iter_files`、`read_text`、timeout 和用户参数交给私有搜索函数。
- 私有函数通过 callbacks/Protocol 协作，不反向 import `WorkspaceFiles`，
  避免循环依赖。
- exception 类型、deadline 覆盖范围、排序、glob/regex/literal 行为和
  max-results 截断必须与拆分前一致。
- `files.py`、`_text_search.py` 均不超过 300 行，所有函数不超过 50 行。

### Step 3：机械拆分 bounded process capture

把 `_git_process.py` 的可变 capture 状态和 pipe reader/cleanup/orchestration
拆为职责单一的私有 dataclass/class 或小函数：

- 公开 `ProcessCapture`、`collect_bounded_output(...)` 签名和返回语义不变。
- 继续并发 drain stdout/stderr，共享同一累计预算。
- reader/thread 构造或启动失败、timeout、超限、read error 的 kill、bounded
  wait、pipe close 和 join 顺序不得改变。
- 每个 helper 不超过 50 行；不得放宽 `_MAX_CLEANUP_SECONDS`。

### Step 4：验证无行为变化

```powershell
& $python -m unittest src.code_agent.workspace.tests.test_files -v
& $python -m unittest src.code_agent.workspace.tests.test_git_limits -v
& $python -m unittest discover -s src/code_agent/workspace/tests -p "test_*.py" -v
& $python -m compileall -q src/code_agent/workspace
git diff --check
```

### Step 5：提交前置拆分

```powershell
git add src/code_agent/workspace/files.py `
  src/code_agent/workspace/_text_search.py `
  src/code_agent/workspace/_git_process.py
git commit -m "拆分工作区长单元：满足粒度约束"
```

---

## Task 1：实现确定性文件状态和 typed edit 前后镜像

**Files:**

- Create: `src/code_agent/workspace/rewind_state.py`
- Create: `src/code_agent/workspace/tests/test_rewind_state.py`

### Step 1：先写失败测试

在 `test_rewind_state.py` 建立临时工作区、`WorkspacePathGuard` 和 `WorkspaceEditor`，覆盖下列精确行为：

- `test_prepare_edit_state_preserves_missing_preimage`
  - 对缺失文件的 write plan，`before.existed` 为 false、hash 为 `None`、size 为 0。
  - `snapshot` 保存精确的缺失前镜像。
  - `after` hash 等于 `after_text.encode("utf-8")` 的 SHA-256。
- `test_prepare_edit_state_preserves_dirty_binary_preimage`
  - 文件包含非 UTF-8 bytes 时，before hash 和 size 直接来自原始 bytes。
- `test_prepare_edit_state_rejects_stale_plan`
  - plan 建立后改变文件 bytes，必须抛出 `EditConflictError`。
- `test_prepare_edit_state_never_applies_the_plan`
  - mock `editor.apply`；函数不得调用它，目标文件保持不变。
- `test_observe_file_states_sorts_canonical_paths`
  - 任意输入顺序都返回按 canonical relative path 排序的 tuple。
- `test_observe_file_states_rejects_duplicate_canonical_paths`
  - 同一路径的重复表示必须在读取结果暴露前拒绝。
- `test_observation_budget_failure_returns_no_partial_state`
  - 累计原始 bytes 超限时抛出 `FileTooLargeError`，不返回前缀状态。
- `test_relevant_digest_is_stable_for_equivalent_order`
  - 相同状态集合的不同 tuple 顺序得到同一摘要。
- `test_relevant_digest_changes_with_existence_hash_or_size`
  - existence、hash 或 size 的任一变化都改变摘要。
- `test_models_are_frozen_and_validate_invariants`
  - 拒绝非 canonical path、`existed=False` 却带 hash/非零 size，以及 `existed=True` 却无合法小写 SHA-256。

测试不得依赖私有 `_read_current`，只从公开 editor/guard 边界构造事实。

### Step 2：验证 RED

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest discover -s src/code_agent/workspace/tests -p "test_rewind_state.py" -v
```

预期：因 `code_agent.workspace.rewind_state` 尚不存在而失败。

### Step 3：实现最小公开单元

在 `rewind_state.py` 定义：

```python
@dataclass(frozen=True)
class WorkspaceFileState:
    relative_path: str
    existed: bool
    sha256: str | None
    size: int

@dataclass(frozen=True)
class PreparedEditState:
    snapshot: WorkspaceSnapshot
    before: WorkspaceFileState
    after: WorkspaceFileState

def prepare_edit_state(
    editor: WorkspaceEditor,
    plan: EditPlan,
    *,
    max_total_bytes: int = DEFAULT_SNAPSHOT_BYTES,
) -> PreparedEditState: ...

def observe_file_states(
    editor: WorkspaceEditor,
    paths: tuple[str, ...],
    *,
    max_total_bytes: int = DEFAULT_SNAPSHOT_BYTES,
) -> tuple[WorkspaceFileState, ...]: ...

def relevant_path_digest(states: tuple[WorkspaceFileState, ...]) -> str: ...
```

实现规则：

1. 严格校验 editor、plan、paths tuple 和 byte budget 的类型/范围，bool 不得冒充 int。
2. 使用 guard 将输入规范化为 workspace-relative POSIX path；用平台路径 identity（Windows 大小写不敏感）拒绝重复。
3. 排序 canonical path 后，一次调用 `editor.snapshot(..., max_total_bytes=...)`；只有完整 snapshot 成功后才构建返回 tuple。
4. `prepare_edit_state` 同样先 snapshot plan 的唯一路径，再把 snapshot existence/hash 与 `plan.existed`、`plan.before_sha256` 比较；不一致抛 `EditConflictError`。
5. before 从 snapshot 的原始 bytes 计算；after 只从 `plan.after_text.encode("utf-8")` 计算，路径与 before 相同且 `existed=True`。
6. `WorkspaceFileState` 在 `__post_init__` 中约束 canonical path、精确 bool/int、非负 size 和 existence/hash 一致性。
7. `relevant_path_digest` 先校验 tuple、元素类型及 canonical identity 唯一性，再对按 path 排序的字段对象做 `ensure_ascii=False`、`sort_keys=True`、紧凑分隔符的 canonical JSON SHA-256；不得依赖调用顺序或对象 repr。

不要修改接近上限的 `edits.py`。

### Step 4：验证 GREEN

```powershell
& $python -m unittest discover -s src/code_agent/workspace/tests -p "test_rewind_state.py" -v
& $python -m unittest discover -s src/code_agent/workspace/tests -p "test_*.py" -v
& $python -m compileall -q src/code_agent/workspace
git diff --check
```

### Step 5：提交本任务

```powershell
git add src/code_agent/workspace/rewind_state.py src/code_agent/workspace/tests/test_rewind_state.py
git commit -m "新增工作区回溯状态：冻结确定性文件事实"
```

---

## Task 2：区分快照 artifact 缺失与完整性损坏

**Files:**

- Modify: `src/code_agent/workspace/errors.py`
- Modify: `src/code_agent/workspace/_guarded_read.py`
- Modify: `src/code_agent/workspace/_snapshot_artifacts.py`
- Modify: `src/code_agent/workspace/snapshot_store.py`
- Create: `src/code_agent/workspace/_snapshot_manifest.py`
- Create: `src/code_agent/workspace/tests/test_snapshot_error_classification.py`
- Modify only if an old assertion must be narrowed: `src/code_agent/workspace/tests/test_snapshot_store_integrity.py`

`snapshot_store.py` 当前 288 行，不能继续堆叠职责。把 manifest 的 canonical JSON、stored-entry 建模、解码和结构/总量验证移动到 `_snapshot_manifest.py`；保留 `SnapshotHandle` 从原公开模块导入的兼容性。

### Step 1：先写失败分类测试

在新的 `test_snapshot_error_classification.py` 覆盖：

- `test_missing_manifest_raises_snapshot_missing`
- `test_missing_blob_raises_snapshot_missing`
- `test_tampered_manifest_raises_snapshot_integrity`
- `test_noncanonical_manifest_raises_snapshot_integrity`
- `test_tampered_blob_size_or_digest_raises_snapshot_integrity`
- `test_handle_manifest_mismatch_raises_snapshot_integrity`
- `test_workspace_fingerprint_mismatch_raises_snapshot_integrity`
- `test_manifest_over_read_budget_raises_snapshot_integrity`
- `test_permission_or_identity_failure_is_not_reported_as_missing`
- `test_disappearing_after_open_is_integrity_not_missing`
- `test_workspace_fingerprint_property_is_stable_and_read_only`

测试规则：

- missing 测试只删除原本成功保存的固定 manifest/blob。
- 权限/身份测试覆盖 `PermissionError`、链接/reparse 拒绝和一般
  `WorkspaceError`，并断言得到 `SnapshotIntegrityError` 而不是
  `SnapshotMissingError`。
- “打开后消失”测试让初始 `os.open()` 成功，再 mock
  `_guarded_read._verify_handle` 抛出 `FileNotFoundError`；它必须分类为
  integrity。这是防止 cause-only 误判的直接回归。
- fingerprint 与另一个路径不同的 workspace 值不同，且对 property 赋值失败。
- 原有广义 `WorkspaceError` 断言仍可成立，因为两个新错误都是其子类；若旧测试对 manifest read 超限断言 `FileTooLargeError`，改为更精确的 `SnapshotIntegrityError`。
- 增加 save 预算回归：保存超 byte/manifest budget 仍为
  `FileTooLargeError`，不得被 load 专用分类改变。

### Step 2：验证 RED

```powershell
& $python -m unittest src.code_agent.workspace.tests.test_snapshot_error_classification -v
```

预期：专用错误或只读 property 尚不存在。

### Step 3：实现错误类型和 artifact 边界分类

在 `errors.py` 增加：

```python
class SnapshotMissingError(WorkspaceError):
    """A referenced snapshot manifest or blob does not exist."""

class SnapshotIntegrityError(WorkspaceError):
    """A snapshot artifact exists but fails integrity validation."""
```

在 `_guarded_read.py` 和 `_snapshot_artifacts.py` 的受保护读取边界：

1. 在 `_guarded_read.py` 定义私有 missing 哨兵（`WorkspaceError` 子类），
   并把**只有初始 `os.open(expected, flags)`** 的调用拆入小 helper。只有
   该调用直接抛出的 `FileNotFoundError` 转为此哨兵。
2. `guard.resolve`、链接/reparse 检查、`fstat`、`_verify_handle`、打开后
   `lstat` 和 bounded read 的失败继续按一般 `WorkspaceError` 传播。
   因此成功打开后路径消失绝不等价于“引用的 artifact 起初不存在”。
3. `_snapshot_artifacts.py` 只把该私有哨兵转成
   `SnapshotMissingError`；不得通过检查任意 `__cause__`、错误消息或
   `Path.exists()` 猜测 missing。
4. manifest 和 blob 的 missing 消息需稳定区分 artifact 类别，但不得
   泄露任意外部路径。
5. 把 artifact 读取拆成 raw 与 referenced 两层：
   `read_manifest/read_blob` 使用 referenced 分类；`ensure_blob()` 的
   exists→read 竞态继续使用 raw 语义，保持 save 不产生 load 专用的
   `SnapshotMissingError`。

### Step 4：拆分 manifest 验证并实现 load 失败闭合

`_snapshot_manifest.py` 承担纯逻辑：

- stored entry 不可变模型；
- canonical JSON 编码和严格 UTF-8/canonical 解码；
- 精确字段、version、identifier、workspace fingerprint、entry 类型、canonical path、duplicate identity、paths/total/handle 一致性验证；
- digest 格式与 missing-entry 不变量；
- `max_total_bytes` 约束。

`WorkspaceSnapshotStore`：

1. 仍负责 guard/path canonicalization、artifact I/O、save/load 编排和 public `SnapshotHandle`。
2. 新增只读 `workspace_fingerprint` property，不暴露 setter。
3. `load()` 的 handle 类型误用仍抛 `TypeError`；进入 artifact load 后：
   - `SnapshotMissingError` 原样传播；
   - manifest/blob/path/handle/fingerprint/size/digest/UTF-8/canonical/预算等任何验证失败统一为 `SnapshotIntegrityError`；
   - 不把权限、链接/reparse 或 handle identity 失败误报为 missing。
4. `save()` 的调用方类型/预算错误语义不因本任务无意改变。
5. 拆分后 `snapshot_store.py`、`_snapshot_manifest.py` 和所有测试文件均不得超过 300 行。

### Step 5：验证 GREEN 与回归

```powershell
& $python -m unittest src.code_agent.workspace.tests.test_snapshot_error_classification -v
& $python -m unittest src.code_agent.workspace.tests.test_snapshot_store -v
& $python -m unittest src.code_agent.workspace.tests.test_snapshot_store_integrity -v
& $python -m unittest discover -s src/code_agent/workspace/tests -p "test_*.py" -v
& $python -m compileall -q src/code_agent/workspace
git diff --check
```

额外检查：

```powershell
Get-ChildItem src/code_agent/workspace -Recurse -Filter *.py |
  ForEach-Object { [pscustomobject]@{ Lines=(Get-Content $_.FullName).Count; Path=$_.FullName } } |
  Where-Object Lines -gt 300
```

预期无输出。

### Step 6：提交本任务

```powershell
git add src/code_agent/workspace/errors.py `
  src/code_agent/workspace/_guarded_read.py `
  src/code_agent/workspace/_snapshot_artifacts.py `
  src/code_agent/workspace/_snapshot_manifest.py `
  src/code_agent/workspace/snapshot_store.py `
  src/code_agent/workspace/tests/test_snapshot_error_classification.py `
  src/code_agent/workspace/tests/test_snapshot_store_integrity.py
git commit -m "细化快照错误：区分缺失与完整性损坏"
```

暂存前先用 `git diff --name-only` 移除实际未修改的可选文件。

---

## Task 3：更新 Workspace 契约并完成 Feature 验收

**Files:**

- Modify: `src/code_agent/workspace/AGENTS.md`

### Step 1：更新 Units

只更新 Units，不改变已批准的目标/边界。至少记录：

- `WorkspaceFileState`、`PreparedEditState`、`prepare_edit_state(...)`
- `observe_file_states(...)`、`relevant_path_digest(...)`
- `SnapshotMissingError`、`SnapshotIntegrityError`
- `WorkspaceSnapshotStore.workspace_fingerprint`
- 私有 manifest 单元属于 snapshot store 的内部协作关系，不新增越界职责

描述需包含：

- 原始 bytes/existence、canonical path、确定性 hash；
- bounded observation 与无部分结果；
- typed edit 只准备、不 apply；
- missing 与 corrupt 的稳定区分；
- Workspace 不负责 journal、顺序、action lineage 或最终可用性判断。

### Step 2：运行完整验收

```powershell
& $python -m unittest discover -s src/code_agent/workspace/tests -p "test_*.py" -v
& $python -m unittest discover -s tests -p "test_*.py" -v
& $python -m compileall -q src/code_agent/workspace
git diff --check
git status --short
```

再检查：

- 所有 Workspace Python 文件不超过 300 行；
- 所有 Workspace 函数（包括既有函数）不超过 50 行；
- `rg -n "editor\\.apply|\\.restore\\(" src/code_agent/workspace/rewind_state.py` 无命中；
- `SnapshotHandle` 仍可从 `code_agent.workspace.snapshot_store` 导入；
- 工作区外没有本阶段产生的修改。

用 AST 自动化验收行数，脚本应无输出并以 0 退出：

```powershell
@'
import ast
from pathlib import Path

violations = []
for path in Path("src/code_agent/workspace").rglob("*.py"):
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) > 300:
        violations.append(f"{path}: file has {len(lines)} lines")
    tree = ast.parse("\n".join(lines), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            size = (node.end_lineno or node.lineno) - node.lineno + 1
            if size > 50:
                violations.append(f"{path}:{node.lineno} {node.name} has {size} lines")
if violations:
    raise SystemExit("\n".join(violations))
'@ | & $python -
```

### Step 3：提交契约

```powershell
git add src/code_agent/workspace/AGENTS.md
git commit -m "更新工作区契约：记录回溯状态单元"
```

### Step 4：独立终审

安排两个全新 reviewer：

1. 规格 reviewer：逐项核对本计划、上游 Task 4、Workspace 边界和所有测试。
2. 质量 reviewer：检查异常因果链、TOCTOU、路径 identity、raw bytes、预算失败闭合、公开导入兼容性、行数/函数上限和全套回归。

任一 reviewer 报告 Critical/Important 时，回到对应实现任务修复、补回归测试、提交，再重新做两类终审；只有两者均 PASS 才进入 Core Feature。
