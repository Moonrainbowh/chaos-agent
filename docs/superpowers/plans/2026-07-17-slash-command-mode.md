# Slash Command Mode Switching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 精简 TUI 斜杠命令并提供一级铺开的 `/模式 low|medium|high|ultra` 空闲切换。

**Architecture:** interfaces 使用单一注册表和注入式 `ModeControl` 表达命令；Windows 组合层按 mode snapshot 重建 provider-bound runner，并在成功后更新 TUI 能力视图。活动任务不允许切换，权限策略保持不变。

**Tech Stack:** Python 3.10+、asyncio、unittest、现有 ModeRegistry/ProviderRuntimeManager。

---

### Task 1: 精简注册表与帮助

**Files:**
- Modify: `src/code_agent/interfaces/command_registry.py`
- Modify: `src/code_agent/interfaces/tui_commands.py`
- Modify: `src/code_agent/interfaces/windows_tui.py`
- Test: `src/code_agent/interfaces/tests/test_tui_commands.py`
- Test: `src/code_agent/interfaces/tests/test_windows_tui.py`

- [ ] 写数据驱动失败测试，断言注册表只保留确认后的命令，且 Picker 将四个 mode 铺为一级候选。
- [ ] 运行定向测试，确认因旧命令仍存在且 `/模式` 缺失而失败。
- [ ] 最小修改注册表、枚举、解析和帮助渲染。
- [ ] 运行定向测试并确认通过。

### Task 2: ModeControl 与命令面板

**Files:**
- Create: `src/code_agent/interfaces/mode_control.py`
- Modify: `src/code_agent/interfaces/picker.py`
- Modify: `src/code_agent/interfaces/tui_interactions.py`
- Modify: `src/code_agent/interfaces/windows_tui.py`
- Test: `src/code_agent/interfaces/tests/test_mode_control.py`
- Test: `src/code_agent/interfaces/tests/test_windows_tui.py`

- [ ] 写失败测试，覆盖四档摘要、空闲切换、运行中拒绝和 `/模式` 二级候选。
- [ ] 运行测试确认缺少 `ModeControl`。
- [ ] 实现注入式 `ModeControl` 和 mode picker items。
- [ ] 接入 `/模式` 处理器与状态栏模型刷新，运行测试确认通过。

### Task 3: Windows 运行时切换集成

**Files:**
- Modify: `code_agent_win/app.py`
- Modify: `code_agent_win/app_ui.py`
- Modify: `code_agent_win/AGENTS.md`
- Modify: `src/code_agent/interfaces/AGENTS.md`
- Test: `tests/test_agent_app.py`

- [ ] 写失败集成测试，断言 mode 切换重建 runner、更新 mode/model，并在重建失败时保留旧状态。
- [ ] 运行测试确认当前应用没有 mode 控制入口。
- [ ] 将 mode snapshot 纳入 provider runtime 构建和原子替换回调，注入 `ModeControl`。
- [ ] 更新 Feature 与集成契约，运行定向测试确认通过。

### Task 4: 完整验证

**Files:**
- Verify only.

- [ ] 运行 `python -m unittest discover -s src/code_agent/interfaces/tests -p 'test_*.py'`。
- [ ] 运行 `python -m unittest discover -s tests -p 'test_*.py'`。
- [ ] 运行 `git diff --check`，检查只修改计划范围且保留既有用户改动。
