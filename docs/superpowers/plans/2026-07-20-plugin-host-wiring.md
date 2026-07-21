# Plugin Host Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> 实施状态（2026-07-20）：tool/command/mode/custom Agent/event/Host interaction 已完成自动化实现和接线；真实插件 TUI 人工验收仍归总计划 Task 9。

**Goal:** 让 Plugin command、mode、custom agent、event 和 UI interaction 与现有 tool 一样进入可审计、可撤销且不可绕过策略的 Host 运行链。

**Architecture:** Plugin Feature 生成不可变贡献 snapshot 和 Host-neutral invocation/proposal；Orchestration 与 Interfaces 接受验证后的 namespaced 贡献；根级 PluginRuntime 连接 Dispatcher、Controller、事件源和 InteractionBroker。

**Tech Stack:** Python 3.10、asyncio、unittest、现有 declarative JSON manifest。

---

### Task 1: 更新契约

**Files:**
- Modify: `src/code_agent/plugins/AGENTS.md`
- Modify: `src/code_agent/orchestration/AGENTS.md`
- Modify: `src/code_agent/interfaces/AGENTS.md`

- [ ] 锁定 namespaced command/mode/agent、Proposal 中央策略、InteractionBroker、snapshot/revoke 和两级线程限制。

### Task 2: Plugin command invocation

**Files:**
- Modify: `src/code_agent/plugins/models.py`
- Modify: `src/code_agent/plugins/registry.py`
- Create: `src/code_agent/plugins/commands.py`
- Create: `src/code_agent/plugins/tests/test_commands.py`

- [ ] **Step 1: 写失败测试**

```python
item = catalog.commands()[0]
assert item.command_id == "review-helper.review"
assert item.display == "/review-helper.review"
```

验证裸 ID、内置覆盖、未知 controller 和超限参数被拒绝。

- [ ] **Step 2: 实现 `PluginCommandInvocation` 和不可变 command catalog**
- [ ] **Step 3: 运行 Plugin 测试**

### Task 3: 动态命令注册表

**Files:**
- Modify: `src/code_agent/interfaces/command_registry.py`
- Modify: `src/code_agent/interfaces/picker.py`
- Modify: `src/code_agent/interfaces/tui_commands.py`
- Modify: `src/code_agent/interfaces/tests/test_tui_commands.py`
- Modify: `src/code_agent/interfaces/tests/test_interaction_v2.py`

- [ ] **Step 1: 写 snapshot 注册/撤销失败测试**
- [ ] **Step 2: 写 namespaced help、filter、completion 和禁用原因测试**
- [ ] **Step 3: 把静态 `_SPECS` 包装为 built-in snapshot，动态贡献不可覆盖**
- [ ] **Step 4: 一次 Picker 打开期间冻结 catalog generation**
- [ ] **Step 5: 运行 Interfaces 测试**

### Task 4: Namespaced Plugin mode

**Files:**
- Modify: `src/code_agent/orchestration/models.py`
- Modify: `src/code_agent/orchestration/modes.py`
- Modify: `src/code_agent/orchestration/tests/test_modes.py`
- Modify: `src/code_agent/interfaces/mode_control.py`
- Modify: `src/code_agent/interfaces/tests/test_mode_control.py`

- [ ] **Step 1: 写 `plugin.strict` 继承 base profile/预算失败测试**
- [ ] **Step 2: 写提高 reasoning、增加工具、覆盖 built-in 的拒绝测试**
- [ ] **Step 3: 引入稳定 `ModeId`/namespaced selection，不改变内置 `AgentMode` enum**
- [ ] **Step 4: ModeControl 接受附加 snapshot 且只在 idle 切换**
- [ ] **Step 5: 运行 Orchestration、Interfaces 测试**

### Task 5: Plugin custom agent

**Files:**
- Modify: `src/code_agent/plugins/registry.py`
- Modify: `src/code_agent/orchestration/models.py`
- Modify: `src/code_agent/orchestration/tests/test_modes.py`
- Modify: `src/code_agent/plugins/tests/test_registry.py`

- [ ] **Step 1: 写 namespaced AgentDefinition 构造失败测试**
- [ ] **Step 2: 写 role/agent_id 互斥、工具收紧、may_write 和禁止 child 测试**
- [ ] **Step 3: 实现 `PluginAgentCatalog.resolve(agent_id)`**
- [ ] **Step 4: 运行 Plugin/Orchestration 测试**

### Task 6: Event Proposal 执行协调

**Files:**
- Modify: `src/code_agent/plugins/events.py`
- Create: `src/code_agent/plugins/runtime.py`
- Modify: `src/code_agent/plugins/tests/test_events.py`
- Create: `src/code_agent/plugins/tests/test_runtime.py`

- [ ] **Step 1: 写敏感字段投影、32 Proposal、深度 2 测试**
- [ ] **Step 2: 写一个 Proposal 失败不影响原事件/其他 Proposal 测试**
- [ ] **Step 3: 定义 Host-neutral Protocol**

```python
class ProposalExecutor(Protocol):
    async def execute_action(self, proposal, context, cancellation): ...
    async def execute_ui(self, proposal, context, cancellation): ...
```

- [ ] **Step 4: 实现稳定结果和脱敏错误类别，不透传异常文本**
- [ ] **Step 5: 运行 Plugin 测试**

### Task 7: 通用 Host Interaction

**Files:**
- Modify: `src/code_agent/interfaces/interaction.py`
- Modify: `src/code_agent/interfaces/tui_interactions.py`
- Modify: `src/code_agent/interfaces/windows_tui.py`
- Modify: `src/code_agent/interfaces/tests/test_interaction_v2.py`
- Modify: `src/code_agent/interfaces/tests/test_windows_tui.py`

- [ ] **Step 1: 写 notify/confirm/input/select 失败 TUI 测试**
- [ ] **Step 2: 写 Esc、CancellationToken、关闭 TUI 和重复 ID 测试**
- [ ] **Step 3: 用统一 pending interaction 取代仅 approval 的专用渲染分支**
- [ ] **Step 4: ApprovalBroker 通过适配器继续复用相同行为**
- [ ] **Step 5: 运行 Interfaces 测试**

### Task 8: Snapshot、revoke 与 digest

**Files:**
- Modify: `src/code_agent/plugins/registry.py`
- Modify: `src/code_agent/plugins/tests/test_registry.py`

- [ ] **Step 1: 写 active task 不 apply staged snapshot 测试**
- [ ] **Step 2: 写 revoke 立即阻止 command/mode/agent/event/tool 测试**
- [ ] **Step 3: 写 digest 漂移后旧贡献不可调用测试**
- [ ] **Step 4: 实现 generation 和统一 `is_active(plugin_id, digest)` 检查**
- [ ] **Step 5: 运行 Plugin 测试**

### Task 9: 根级 PluginRuntime 集成

**Files:**
- Modify: `code_agent_win/plugin_runtime.py`
- Modify: `code_agent_win/app.py`
- Modify: `code_agent_win/action_dispatcher.py`
- Modify: `code_agent_win/agent_modes.py`
- Modify: `code_agent_win/subagents.py`
- Modify: `code_agent_win/tools.py`
- Modify: `tests/test_agent_app.py`
- Modify: `tests/test_command_integration.py`
- Modify: `tests/test_subagent_integration.py`

- [ ] **Step 1: 写 command→Controller 失败集成测试**
- [ ] **Step 2: 写 mode 下一任务生效测试**
- [ ] **Step 3: 写 `delegate_agent.agent_id` 集成测试**
- [ ] **Step 4: 写 event→policy→interaction/action 集成测试**
- [ ] **Step 5: 连接 task/session/review/workflow Controller adapters**
- [ ] **Step 6: Dispatcher 对 Plugin 原动作和目标动作继续双重评估**
- [ ] **Step 7: 运行全部 Plugin、Orchestration、Interfaces 和根级测试**
