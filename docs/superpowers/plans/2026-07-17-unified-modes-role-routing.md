# Unified Modes and Role Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give all four task modes the same tools and Ultra-sized hard limits while routing the main model by mode and child models by role.

**Architecture:** Keep mode, permission, and provider concerns separate. `ModeRegistry` continues to freeze auditable mode snapshots; Windows composition supplies one common tool/limit envelope, while the child runtime converts each supported role to a stable role mode whose configured profile selects DeepSeek, Terra, or Sol.

**Tech Stack:** Python 3.10+, `unittest`, existing Chaos Agent orchestration and Windows composition modules.

---

### Task 1: Freeze the shared mode contract

**Files:**
- Modify: `src/code_agent/orchestration/AGENTS.md`
- Modify: `code_agent_win/AGENTS.md`

- [x] **Step 1: Record the requirements**

Document that mode never changes tool availability or hard limits, and that child provider selection is role-based rather than inherited from the parent mode.

- [x] **Step 2: Check contract consistency**

Run: `rg -n "工具|预算|角色|profile" src/code_agent/orchestration/AGENTS.md code_agent_win/AGENTS.md`

Expected: both contracts describe the shared envelope and role routing without granting permission.

### Task 2: Unify mode tools, limits, and main-agent policy

**Files:**
- Modify: `src/code_agent/orchestration/tests/test_modes.py`
- Modify: `src/code_agent/orchestration/modes.py`
- Modify: `tests/test_agent_app.py`
- Modify: `code_agent_win/agent_modes.py`
- Modify: `code_agent_win/app.py`

- [x] **Step 1: Write failing tests**

Assert that the four standard definitions share the Ultra `EngineLimits`, that every Windows mode exposes `ALL_TOOLS`, that reasoning remains low/medium/high/xhigh, and that plugin tools are not gated by mode.

- [x] **Step 2: Run tests to verify RED**

Run: `python -m unittest src.code_agent.orchestration.tests.test_modes tests.test_agent_app -v`

Expected: failures show distinct limits, restricted low/medium tools, or mode-gated plugin tools.

- [x] **Step 3: Implement the shared envelope**

Use one immutable Ultra-sized `EngineLimits` value for all definitions, bind `ALL_TOOLS` to every Windows mode, retain distinct reasoning and prompt policies, and append available plugin tools for every main mode.

- [x] **Step 4: Run tests to verify GREEN**

Run: `python -m unittest src.code_agent.orchestration.tests.test_modes tests.test_agent_app -v`

Expected: all selected tests pass.

### Task 3: Route child models by role

**Files:**
- Modify: `tests/test_subagent_integration.py`
- Modify: `code_agent_win/agent_modes.py`
- Modify: `code_agent_win/subagents.py`
- Modify: `code_agent_win/app.py`

- [x] **Step 1: Write failing role-routing tests**

Assert `search` and `librarian` use Low/DeepSeek, ordinary `subagent` uses Medium/Terra, and `review` and `oracle` use High/Sol regardless of parent mode.

- [x] **Step 2: Run the tests to verify RED**

Run: `python -m unittest tests.test_subagent_integration -v`

Expected: the existing parent-derived child mode does not satisfy the role matrix.

- [x] **Step 3: Implement minimal role routing**

Replace `default_child_mode(parent_mode)` with `child_mode_for_role(role)` and have `SubagentTool` select the child snapshot after parsing the requested role.

- [x] **Step 4: Run the tests to verify GREEN**

Run: `python -m unittest tests.test_subagent_integration -v`

Expected: all child integration tests pass.

### Task 4: Verify the integrated behavior

**Files:**
- Modify: `docs/superpowers/plans/2026-07-17-unified-modes-role-routing.md`

- [x] **Step 1: Run the complete test suite**

Run: `python -m unittest discover -s tests -p 'test_*.py'`

Expected: zero failures and zero errors.

- [x] **Step 2: Run feature tests**

Run: `python -m unittest discover -s src/code_agent/orchestration/tests -p 'test_*.py'`

Expected: zero failures and zero errors.

- [x] **Step 3: Inspect the final diff**

Run: `git diff --check` and `git diff --stat`

Expected: no whitespace errors and only scoped contract, orchestration, integration, test, and plan changes.
