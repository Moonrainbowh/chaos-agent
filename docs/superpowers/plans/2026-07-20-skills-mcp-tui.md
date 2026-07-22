# Skills 与 MCP TUI Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> 实施状态（2026-07-20）：thread-scoped Skills、MCP lifecycle、动态工具和 TUI/Picker 已完成自动化实现与真实 stdio fixture 测试；真实用户配置人工验收仍归总计划 Task 9。

**Goal:** 通过统一命令注册表和二级 Picker 完成 Skills 与预配置 MCP server 的发现、查询、启停、恢复和诊断控制面。

**Architecture:** Skills Feature 管理发现、digest 和激活状态；Sessions 保存 thread-scoped activation；MCP Feature 管理已批准 server 生命周期和动态工具 snapshot；Interfaces 只调用 Controller 并渲染脱敏结果。

**Tech Stack:** Python 3.10、asyncio、SQLite、unittest、MCP Python SDK。

---

### Task 1: 更新契约

**Files:**
- Modify: `src/code_agent/skills/AGENTS.md`
- Modify: `src/code_agent/mcp/AGENTS.md`
- Modify: `src/code_agent/interfaces/AGENTS.md`
- Modify: `src/code_agent/sessions/AGENTS.md`

- [ ] 锁定命令集合、thread-scoped Skill activation、MCP 会话态启停、动态工具边界和统一 Picker。

### Task 2: Skill activation 持久化

**Files:**
- Modify: `src/code_agent/sessions/_database.py`
- Create: `src/code_agent/sessions/_skills.py`
- Modify: `src/code_agent/sessions/repository.py`
- Modify: `src/code_agent/sessions/tests/test_migrations.py`
- Create: `src/code_agent/sessions/tests/test_skills.py`

- [ ] **Step 1: 写 schema 11→12 失败迁移测试**
- [ ] **Step 2: 新增 `skill_activations(thread_id, skill_id, source, digest, activated_at)`**
- [ ] **Step 3: 写 upsert/list/remove、FK 和并发测试**
- [ ] **Step 4: 实现 Repository mixin**
- [ ] **Step 5: 运行 Sessions 测试**

### Task 3: SkillController

**Files:**
- Modify: `src/code_agent/skills/registry.py`
- Create: `src/code_agent/skills/controller.py`
- Create: `src/code_agent/skills/tests/test_controller.py`
- Modify: `src/code_agent/skills/tests/test_registry.py`

- [ ] **Step 1: 写 list/info/source/reload 失败测试**
- [ ] **Step 2: 写用户 Skill 激活、工作区确认、禁用测试**
- [ ] **Step 3: 写恢复 digest 一致和漂移停用测试**
- [ ] **Step 4: 定义 Host-neutral `SkillApproval` Protocol**
- [ ] **Step 5: 实现 Controller；完整 Skill 文本不进入 Sessions**
- [ ] **Step 6: 运行 Skills 测试**
- [ ] **Step 7: 更新 Skills AGENTS Units**

### Task 4: `/技能` 命令与 Picker

**Files:**
- Modify: `src/code_agent/interfaces/command_registry.py`
- Modify: `src/code_agent/interfaces/picker.py`
- Create: `src/code_agent/interfaces/tui_skill_commands.py`
- Modify: `src/code_agent/interfaces/tui_commands.py`
- Modify: `src/code_agent/interfaces/windows_tui.py`
- Modify: `src/code_agent/interfaces/tests/test_tui_commands.py`
- Modify: `src/code_agent/interfaces/tests/test_interaction_v2.py`

- [ ] **Step 1: 写六个 `/技能` 动作解析失败测试**
- [ ] **Step 2: 写 Skill ID 二级候选、冲突禁用原因测试**
- [ ] **Step 3: 注册动态 action provider，不把 Skill 内容放入 Picker**
- [ ] **Step 4: handler 只委托 SkillController**
- [ ] **Step 5: 运行 Interfaces 测试**

### Task 5: MCP 状态和诊断模型

**Files:**
- Modify: `src/code_agent/mcp/registry.py`
- Modify: `src/code_agent/mcp/stdio_manager.py`
- Modify: `src/code_agent/mcp/official_sdk.py`
- Modify: `src/code_agent/mcp/tests/test_registry.py`
- Modify: `src/code_agent/mcp/tests/test_stdio_manager.py`

- [ ] **Step 1: 写状态、工具列表、健康、脱敏诊断失败测试**
- [ ] **Step 2: 写 enable handshake 成功后才暴露工具测试**
- [ ] **Step 3: 写 disable/crash 立即撤下工具并取消 in-flight 测试**
- [ ] **Step 4: 写 restart、超时、stderr 上限和 schema 非法测试**
- [ ] **Step 5: 实现不可变 `McpSnapshot(generation, servers, tools)`**
- [ ] **Step 6: 运行 MCP 测试**

### Task 6: `/mcp` 命令与 Picker

**Files:**
- Modify: `src/code_agent/interfaces/command_registry.py`
- Modify: `src/code_agent/interfaces/picker.py`
- Modify: `src/code_agent/interfaces/tui_mcp_commands.py`
- Modify: `src/code_agent/interfaces/tui_commands.py`
- Modify: `src/code_agent/interfaces/windows_tui.py`
- Modify: `src/code_agent/interfaces/tests/test_tui_commands.py`
- Modify: `src/code_agent/interfaces/tests/test_interaction_v2.py`

- [ ] **Step 1: 写七个 `/mcp` 动作解析失败测试**
- [ ] **Step 2: 写 server 二级候选、未批准禁用原因测试**
- [ ] **Step 3: 写启用、禁用、重启、诊断 handler 测试**
- [ ] **Step 4: 把现有孤立 `handle_mcp_command` 接入统一注册表**
- [ ] **Step 5: 运行 Interfaces 测试**

### Task 7: 动态工具 snapshot

**Files:**
- Modify: `src/code_agent/mcp/registry.py`
- Modify: `src/code_agent/core/engine.py`
- Modify: `src/code_agent/interfaces/controller.py`
- Create: `src/code_agent/mcp/tests/test_snapshot.py`
- Modify: `src/code_agent/core/tests/test_engine_tools.py`

- [ ] **Step 1: 写同一模型回合不变、下一回合刷新测试**
- [ ] **Step 2: 写 disable 后 provider 不再看到工具测试**
- [ ] **Step 3: Engine 在 TURN_STARTED 冻结 tool tuple 和 generation**
- [ ] **Step 4: Controller 在下一回合读取最新 snapshot**
- [ ] **Step 5: 运行 MCP/Core 测试**

### Task 8: 真实 stdio fixture

**Files:**
- Modify: `src/code_agent/mcp/tests/sdk_fixture_server.py`
- Modify: `src/code_agent/mcp/tests/test_stdio_manager.py`

- [ ] **Step 1: 启动已批准本地 fixture，完成 initialize/list/call**
- [ ] **Step 2: 验证禁用、取消、重启和 close 后无子进程**
- [ ] **Step 3: 验证控制字符和伪 secret 不进入诊断**
- [ ] **Step 4: 运行 MCP suite 两次，确认无端口或进程泄漏**

### Task 9: 根级 Skills/MCP 集成

**Files:**
- Modify: `code_agent_win/app.py`
- Modify: `code_agent_win/app_ui.py`
- Modify: `code_agent_win/action_dispatcher.py`
- Modify: `tests/test_agent_app.py`
- Modify: `tests/test_command_integration.py`

- [ ] **Step 1: 写 Skill activate→context→resume 失败集成测试**
- [ ] **Step 2: 写 digest 漂移后不恢复失败测试**
- [ ] **Step 3: 写 MCP enable→tool visible→disable→removed 失败测试**
- [ ] **Step 4: 连接 SkillController、McpController 和 InteractionBroker**
- [ ] **Step 5: mode/MCP/Plugin 变化只在安全回合边界刷新 runner/tool snapshot**
- [ ] **Step 6: 运行 Skills、MCP、Interfaces、Core 和根级测试**
