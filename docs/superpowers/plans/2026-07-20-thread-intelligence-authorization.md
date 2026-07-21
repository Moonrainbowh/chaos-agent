# Thread Intelligence 与两级授权 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> 实施状态（2026-07-20）：Feature 与根层接线已完成并通过自动化测试；真实超长线程的人工观察仍归总计划 Task 9。

**Goal:** 用真实 thread ID 和 message sequence 生成可持久、可检索的 semantic checkpoint，并让所有 Agent 在两级授权范围内使用 `search_threads`、`read_thread`。

**Architecture:** Sessions 负责父子关系、稳定消息记录、checkpoint/index 事务；Thread Intelligence 负责授权计算、语义压缩协调、搜索和读取。ContextBuilder 协议携带 thread ID 与 cancellation，集成层为每个冻结 profile 注入摘要服务。

**Tech Stack:** Python 3.10、asyncio、SQLite、unittest。

---

### Task 1: 更新 Feature 契约

**Files:**
- Modify: `src/code_agent/context/AGENTS.md`
- Modify: `src/code_agent/core/AGENTS.md`
- Modify: `src/code_agent/orchestration/AGENTS.md`
- Modify: `src/code_agent/sessions/AGENTS.md`
- Modify: `src/code_agent/thread_intelligence/AGENTS.md`

- [ ] 写明 ContextBuilder 的 thread/cancellation 边界、两级授权、稳定 sequence、原子 checkpoint/index、所有角色可见但 Host 裁剪，以及 Workflow Edge 不授权。
- [ ] 本 Task 不修改 Units 或实现文件。

### Task 2: 增加父子线程和语义表迁移

**Files:**
- Modify: `src/code_agent/sessions/_database.py`
- Modify: `src/code_agent/sessions/tests/test_migrations.py`

- [ ] **Step 1: 写失败迁移测试**

测试从 schema 9 数据库打开后具备：

```python
assert "parent_thread_id" in columns(connection, "threads")
assert {"semantic_checkpoints", "thread_index_entries"} <= tables(connection)
assert foreign_key_check(connection) == ()
```

- [ ] **Step 2: 运行并确认因 schema 未升级而失败**

```powershell
python -m unittest discover -s src\code_agent\sessions\tests -p 'test_migrations.py' -v
```

- [ ] **Step 3: 最小迁移**

把 `SCHEMA_VERSION` 增至 10，增加：

```sql
ALTER TABLE threads ADD COLUMN parent_thread_id TEXT REFERENCES threads(id);
CREATE INDEX threads_parent_id ON threads(parent_thread_id);
CREATE TABLE semantic_checkpoints (...);
CREATE TABLE thread_index_entries (...);
```

表中使用明确列保存 source sequence、digest、summary、model、usage、version、relation 和 bounded text；更新 `_REQUIRED_COLUMNS`。

- [ ] **Step 4: 运行 Sessions 测试并通过**

### Task 3: 稳定消息记录和两级线程创建

**Files:**
- Modify: `src/code_agent/sessions/models.py`
- Modify: `src/code_agent/sessions/repository.py`
- Modify: `src/code_agent/sessions/_records.py`
- Modify: `src/code_agent/core/protocols.py`
- Modify: `src/code_agent/sessions/tests/test_repository.py`
- Modify: `src/code_agent/sessions/tests/test_concurrency.py`

- [ ] **Step 1: 写失败测试**

期望 API：

```python
root = await repository.create_thread()
child = await repository.create_thread(parent_thread_id=root)
records = await repository.load_message_records(root)
assert records[0].sequence > 0
assert records[0].message == original
with self.assertRaises(ValueError):
    await repository.create_thread(parent_thread_id=child)
```

- [ ] **Step 2: 确认失败原因是 API/字段缺失**
- [ ] **Step 3: 实现不可变 `MessageRecord(sequence, thread_id, message, created_at)`**
- [ ] **Step 4: 扩展 `create_thread(parent_thread_id=None)`，在单一写事务中验证父存在且无父**
- [ ] **Step 5: 实现 `load_message_records`，严格按数据库 sequence 排序**
- [ ] **Step 6: 并发测试证明不会创建孙线程或悬空 parent**
- [ ] **Step 7: 运行 Sessions 全套测试**

### Task 4: 持久化 checkpoint 与 index

**Files:**
- Create: `src/code_agent/sessions/_thread_intelligence.py`
- Modify: `src/code_agent/sessions/repository.py`
- Modify: `src/code_agent/thread_intelligence/models.py`
- Modify: `src/code_agent/sessions/tests/test_repository.py`

- [ ] **Step 1: 写失败往返和原子性测试**

```python
await repository.publish_semantic_checkpoint(checkpoint, entries)
assert await repository.load_semantic_checkpoints(thread_id) == (checkpoint,)
assert await repository.search_thread_index((thread_id,), "needle", limit=5)
```

通过注入非法 index entry 验证整个事务回滚，checkpoint 不单独出现。

- [ ] **Step 2: 实现显式 codec，不 pickle，不保存原始 reasoning**
- [ ] **Step 3: 实现 publish/load/search/read-source Repository 方法**
- [ ] **Step 4: 对 query、limit、文本大小和 digest 做构造校验**
- [ ] **Step 5: 运行往返、迁移和并发测试**

### Task 5: 两级 ThreadAuthorization

**Files:**
- Create: `src/code_agent/thread_intelligence/authorization.py`
- Create: `src/code_agent/thread_intelligence/tests/test_authorization.py`
- Modify: `src/code_agent/thread_intelligence/AGENTS.md`

- [ ] **Step 1: 写失败测试**

```python
assert await authorization.visible_threads(root) == frozenset({root, child_a, child_b})
assert await authorization.visible_threads(child_a) == frozenset({root, child_a})
assert child_b not in await authorization.visible_threads(child_a)
```

未知 caller 与损坏关系抛出同一个 `ThreadAccessError`，不泄漏目标存在性。

- [ ] **Step 2: 实现 `ThreadRelationStore` Protocol 和 `ThreadAuthorization`**
- [ ] **Step 3: 运行 Thread Intelligence 测试**
- [ ] **Step 4: 更新 Units，记录公开接口与失败闭合规则**

### Task 6: 扩展 ContextBuilder 协议

**Files:**
- Modify: `src/code_agent/core/protocols.py`
- Modify: `src/code_agent/core/engine.py`
- Modify: `src/code_agent/context/builder.py`
- Modify: `src/code_agent/skills/registry.py`
- Modify: `src/code_agent/core/tests/_engine_support.py`
- Modify: `src/code_agent/core/tests/test_engine_run.py`
- Modify: `src/code_agent/context/tests/test_builder.py`

- [ ] **Step 1: 写失败协议传播测试**

Fake builder 记录：

```python
self.calls.append((thread_id, cancellation))
```

断言 Engine 对新线程、恢复线程和子线程传入真实 ID 与当前 token。

- [ ] **Step 2: 修改 Protocol 和全部实现签名**
- [ ] **Step 3: `SkillContextBuilder` 原样转发 thread ID/cancellation**
- [ ] **Step 4: WorkspaceContextBuilder 校验参数但保持确定性行为不变**
- [ ] **Step 5: 运行 Core、Context、Skills 测试**

### Task 7: 语义上下文协调器

**Files:**
- Create: `src/code_agent/thread_intelligence/service.py`
- Modify: `src/code_agent/thread_intelligence/compaction.py`
- Create: `src/code_agent/thread_intelligence/tests/test_service.py`
- Modify: `src/code_agent/thread_intelligence/tests/test_compaction.py`

- [ ] **Step 1: 写未达阈值测试，断言 summarizer 零调用**
- [ ] **Step 2: 写达阈值测试，断言使用真实 message sequence 形成 anchor**
- [ ] **Step 3: 写成功发布测试，断言持久化完成后才返回 semantic prompt**
- [ ] **Step 4: 写摘要/持久化失败测试，断言返回 DeterministicCompactor 结果**
- [ ] **Step 5: 写取消测试，断言 CancellationError 向上传播**
- [ ] **Step 6: 实现 `ThreadAwareContextBuilder`**

核心流程固定为：

```python
records = await store.load_message_records(thread_id)
result = await semantic.compact_records(records, ...)
if result.checkpoint:
    await store.publish_semantic_checkpoint(result.checkpoint, result.index_entries)
return await inner.build(thread_id, result.messages, user_input, tools, task_state, cancellation)
```

- [ ] **Step 7: 运行 Thread Intelligence 与 Context 测试**

### Task 8: 搜索和读取服务

**Files:**
- Create: `src/code_agent/thread_intelligence/tools.py`
- Modify: `src/code_agent/thread_intelligence/reader.py`
- Modify: `src/code_agent/thread_intelligence/index.py`
- Modify: `src/code_agent/thread_intelligence/tests/test_index_reader.py`

- [ ] **Step 1: 写父、子、兄弟、无关线程权限测试**
- [ ] **Step 2: 写 query/limit/source kind 和稳定排序测试**
- [ ] **Step 3: 写 stable anchor digest、supersede/revert/tool result 测试**
- [ ] **Step 4: 实现 `ThreadIntelligenceTools.search(caller_thread_id, ...)` 和 `read(...)`**
- [ ] **Step 5: 保证 caller ID 只由 Host 注入，不在 provider schema 中出现**
- [ ] **Step 6: 运行 Thread Intelligence 全套测试**

### Task 9: 根级集成

**Files:**
- Modify: `code_agent_win/app.py`
- Modify: `code_agent_win/action_dispatcher.py`
- Modify: `code_agent_win/subagents.py`
- Modify: `code_agent_win/tools.py`
- Modify: `tests/test_agent_app.py`
- Modify: `tests/test_subagent_integration.py`
- Modify: `tests/test_tool_schemas.py`

- [ ] **Step 1: 先写失败集成测试，覆盖恢复线程 semantic checkpoint**
- [ ] **Step 2: 先写失败授权测试，覆盖 parent/child/sibling**
- [ ] **Step 3: 为冻结 profile 构造无 Agent 工具循环的 summarizer adapter**
- [ ] **Step 4: 把摘要 Usage 计入当前任务预算**
- [ ] **Step 5: 子 Agent 创建 thread 时传入 parent thread ID**
- [ ] **Step 6: 使用执行 ContextVar 向 Dispatcher 绑定 caller thread**
- [ ] **Step 7: 注册严格 schema 的 `search_threads`、`read_thread`**
- [ ] **Step 8: 运行根级 51 项旧测试及新增测试**
