# Rewind Requirement Boundaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze the four Feature trust boundaries required by preview-only arbitrary-checkpoint rewind without adding implementation code or Units.

**Architecture:** Sessions owns durable facts, Workspace owns deterministic file state, Core owns explicit action lineage, and Interfaces owns pure read-only presentation. Windows composition remains an integration concern and is intentionally unchanged in this requirements stage.

**Tech Stack:** Markdown Feature contracts governed by the repository `AGENTS.md`.

---

## Task 1: Freeze the four Feature boundaries

**Files:**
- Modify: `src/code_agent/sessions/AGENTS.md`
- Modify: `src/code_agent/workspace/AGENTS.md`
- Modify: `src/code_agent/core/AGENTS.md`
- Modify: `src/code_agent/interfaces/AGENTS.md`

- [ ] **Step 1: Add the Sessions boundary**

Append these two bullets to `## 边界` in
`src/code_agent/sessions/AGENTS.md`, preserving every existing `## Units`
entry byte-for-byte:

```markdown
- 负责：以专用表持久化 workspace coverage、prepared/completed mutation、path preimage、代码 owner scope 和 checkpoint mutation 高水位，并提供同事务的有界 rewind observation。
- 不负责：解析 SnapshotHandle、读取工作区、判断当前路径冲突或把 checkpoint metadata 当作可信 rewind 事实。
```

- [ ] **Step 2: Add the Workspace boundary**

Append these two bullets to `## 边界` in
`src/code_agent/workspace/AGENTS.md`, preserving every existing `## Units`
entry byte-for-byte:

```markdown
- 负责：为已规划的 typed edit 生成精确 bytes/existence 前镜像、确定性前后 hash、当前路径状态与相关路径摘要。
- 不负责：Action 归因、checkpoint 排序、Sessions journal 或 rewind 可用性裁决。
```

- [ ] **Step 3: Add the Core boundary**

Append these two bullets to `## 边界` in
`src/code_agent/core/AGENTS.md`, preserving every existing `## Units` entry
byte-for-byte:

```markdown
- 负责：把 owner/origin thread、task、request 和 parent request 作为不可变 Action execution context 显式传给 dispatcher。
- 不负责：工作区快照、mutation journal、coverage 或 rewind UI。
```

- [ ] **Step 4: Add the Interfaces boundary**

Append these two bullets to `## 边界` in
`src/code_agent/interfaces/AGENTS.md`, preserving every existing `## Units`
entry byte-for-byte:

```markdown
- 负责：纯 rewind 模型、稳定禁用原因、候选分页、只读 source 委托和有界安全渲染。
- 不负责：Sessions 查询、snapshot 加载、文件恢复、Git 操作、apply 授权或 provider 调用。
```

- [ ] **Step 5: Verify the requirements-only diff**

Run:

```powershell
git diff -- src/code_agent/sessions/AGENTS.md src/code_agent/workspace/AGENTS.md src/code_agent/core/AGENTS.md src/code_agent/interfaces/AGENTS.md
git diff --check
$changed = git diff --name-only
$expected = @(
  'src/code_agent/core/AGENTS.md',
  'src/code_agent/interfaces/AGENTS.md',
  'src/code_agent/sessions/AGENTS.md',
  'src/code_agent/workspace/AGENTS.md'
)
$unexpected = @($changed | Where-Object { $_ -notin $expected })
if ($unexpected.Count -ne 0) {
  throw "requirements stage modified forbidden files: $unexpected"
}
```

Expected: the four Feature contracts contain only the eight boundary bullets;
no source, test, root integration, or `## Units` content changes.

- [ ] **Step 6: Commit the requirements boundary**

```powershell
git add src/code_agent/sessions/AGENTS.md src/code_agent/workspace/AGENTS.md src/code_agent/core/AGENTS.md src/code_agent/interfaces/AGENTS.md
git commit -m "明确回溯需求：冻结跨 Feature 信任边界"
```

Expected: one requirements-only commit. Do not begin Sessions implementation
in the same commit.
