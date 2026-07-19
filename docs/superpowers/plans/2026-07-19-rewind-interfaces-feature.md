# Rewind Interfaces Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 先消除 Interfaces 既有结构门禁债务，再实现冻结 rewind 模型、纯预览投影、有界安全渲染和只读 `/回溯`、`/rewind` 委托。

**Architecture:** Interfaces 只接收后续 Windows integration 提供的可信纯事实；`rewind_models.py` 冻结协议与不变量，`rewind_view.py` 纯投影并安全渲染，`tui_rewind_commands.py` 只解析参数和调用注入的 `RewindPreviewSource`。任何 Sessions/Workspace 读取、snapshot 验证、Git、provider、approval、apply 或 TUI 接线均留给后续 integration。

**Tech Stack:** Python 3.10+、`dataclasses`、`Enum`、`Protocol`、`unittest`、现有 `CommandRegistry`/`DisplayKind`/`safe_text`。

---

## 适用规范与阶段边界

执行前完整阅读：

- `AGENTS.md`
- `AGENTS.python.md`
- `src/code_agent/interfaces/AGENTS.md`
- `docs/superpowers/specs/2026-07-19-rewind-interfaces-design.md`

本计划分为：

1. 行为保持的 Interfaces 结构前置；
2. Interfaces Feature 实现；
3. Interfaces Units 契约与 Feature gate。

执行前置：本计划必须先由规划阶段单独提交，并从 clean worktree 开始 Task 0。
执行期间本文件只读；checkbox、审查结论和 `$structuralBase` 精确 SHA 记录在
Codex 任务状态中，不写回 `docs/`，因此不会突破 Interfaces 阶段目录边界。

结构前置完成后，以该提交为 rewind 实现阶段基线。后续 rewind 提交不得再修改
`windows_tui.py`、既有 renderer/state 测试，也不得修改 Interfaces 目录以外的
任何文件。

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/code_agent/interfaces/tui_display_commands.py` | 行为保持地承接既有 language/theme/color/glyphs 分派 |
| `src/code_agent/interfaces/tests/test_terminal_renderer.py` | 承接既有 renderer characterization tests |
| `src/code_agent/interfaces/_rewind_model_validation.py` | 共享有界文本、整数、UTC、digest、path 与 tuple 校验 |
| `src/code_agent/interfaces/_rewind_candidate_models.py` | 隔离候选记录与分页不变量，供主模型模块重导出 |
| `src/code_agent/interfaces/rewind_models.py` | 冻结 rewind 枚举、事实、预览、候选分页与只读 source protocol |
| `src/code_agent/interfaces/tests/test_rewind_preview_models.py` | 覆盖 preview facet 分类与 `both` 部分结果拒绝 |
| `src/code_agent/interfaces/rewind_view.py` | 纯 facet 投影与有界安全文本渲染 |
| `src/code_agent/interfaces/tui_rewind_commands.py` | 解析并只读委托 list/preview |
| `src/code_agent/interfaces/tests/test_tui_rewind_boundaries.py` | 验证 import allowlist 与 asyncio 取消传播 |
| `src/code_agent/interfaces/command_registry.py` | rewind 命令与双语 actions 的唯一目录 |
| `src/code_agent/interfaces/tui_commands.py` | 通用命令分类，并为 rewind 保留 raw instruction |
| `src/code_agent/interfaces/command_availability.py` | 根据 host 注入的非空 rewind source 暴露服务 |

---

### Task 0: Freeze the live Interfaces baseline and prove structural RED

**Files:** None

- [ ] **Step 1: Run the existing Interfaces suite**

```powershell
$dirty = git status --porcelain
if ($dirty) {
    throw "Task 0 requires a clean committed plan baseline: $dirty"
}
$python = ".venv\Scripts\python.exe"
& $python -B -m unittest discover `
  -s src/code_agent/interfaces/tests `
  -p "test_*.py" -v
if ($LASTEXITCODE -ne 0) {
    throw "Interfaces baseline failed"
}
```

Expected: `Ran 106 tests` and `OK`.

- [ ] **Step 2: Run the complete structural probe**

```powershell
$python = ".venv\Scripts\python.exe"
$code = @'
from __future__ import annotations

import ast
from pathlib import Path

violations: list[str] = []
for path in sorted(Path("src/code_agent/interfaces").rglob("*.py")):
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    if len(lines) > 300:
        violations.append(f"FILE {path.as_posix()}:{len(lines)}")
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            size = (node.end_lineno or node.lineno) - node.lineno + 1
            if size > 50:
                violations.append(
                    f"FUNC {path.as_posix()}:{node.lineno} "
                    f"{node.name}:{size}"
                )
print("\n".join(violations) or "STRUCTURE OK")
raise SystemExit(bool(violations))
'@
& $python -B -c $code
```

Expected: exit 1 with exactly:

```text
FUNC src/code_agent/interfaces/tests/test_terminal_state.py:71 test_restore_projects_persisted_thread_state:59
FILE src/code_agent/interfaces/tests/test_windows_tui.py:382
FUNC src/code_agent/interfaces/windows_tui.py:182 _handle_command:69
```

Do not commit baseline commands.

---

### Task 0A: Split renderer tests without changing behavior

**Files:**

- Create: `src/code_agent/interfaces/tests/test_terminal_renderer.py`
- Modify: `src/code_agent/interfaces/tests/test_windows_tui.py`

- [ ] **Step 1: Create the dedicated renderer test file**

Create `src/code_agent/interfaces/tests/test_terminal_renderer.py`:

```python
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.interfaces.controller import AgentController  # noqa: E402
from code_agent.interfaces.terminal_display import (  # noqa: E402
    DisplayKind,
    clip_display,
    display_width,
    text_entry,
)
from code_agent.interfaces.terminal_renderer import (  # noqa: E402
    ColorMode,
    Theme,
    render_entries,
    render_entry,
    render_live_tail,
)
from code_agent.interfaces.terminal_state import ApprovalBroker  # noqa: E402
from code_agent.interfaces.terminal_tail import (  # noqa: E402
    render_live_tail_frame,
)
from code_agent.interfaces.tests._support import FakeEngine  # noqa: E402
from code_agent.interfaces.windows_tui import (  # noqa: E402
    WindowsTerminalApp,
    render_terminal,
)


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _plain(value: str) -> str:
    return _ANSI.sub("", value)


class TerminalFirstRendererTests(unittest.TestCase):
    def test_entry_uses_local_ansi_only_and_strips_model_controls(self) -> None:
        entry = text_entry(DisplayKind.ERROR, "bad\x1b[2J\x00")
        rendered = render_entry(entry, 80, color=ColorMode.ALWAYS)
        self.assertIn("\x1b[31m", rendered)
        self.assertNotIn("\x1b[2J", rendered)
        self.assertIn("bad?[2J?", rendered)

    def test_never_color_has_ascii_fallback(self) -> None:
        entry = text_entry(DisplayKind.SUCCESS, "saved")
        rendered = render_entry(
            entry, 80, theme=Theme.SIGNAL, color=ColorMode.NEVER
        )
        self.assertEqual(rendered, "+ saved")
        self.assertNotIn("\x1b", rendered)

    def test_user_agent_and_tool_rows_have_distinct_semantic_colors(self) -> None:
        user = render_entry(
            text_entry(DisplayKind.USER, "request"),
            80,
            color=ColorMode.ALWAYS,
        )
        agent = render_entry(
            text_entry(DisplayKind.AGENT, "answer"),
            80,
            color=ColorMode.ALWAYS,
        )
        tool = render_entry(
            text_entry(DisplayKind.TOOL, "read_file"),
            80,
            color=ColorMode.ALWAYS,
        )
        self.assertIn("38;5;80", user)
        self.assertIn("38;5;80", agent)
        self.assertIn("38;5;250", tool)

    def test_agent_body_is_white_while_its_marker_keeps_semantic_color(self) -> None:
        rendered = render_entry(
            text_entry(DisplayKind.AGENT, "answer"),
            80,
            color=ColorMode.ALWAYS,
        )
        self.assertIn("\x1b[38;5;80m◆\x1b[0m", rendered)
        self.assertIn("\x1b[38;5;252manswer\x1b[0m", rendered)

    def test_tool_records_are_dim_while_task_success_is_green(self) -> None:
        rendered = render_entry(
            text_entry(DisplayKind.TOOL, "read_file completed"),
            80,
            color=ColorMode.ALWAYS,
        )
        completed = render_entry(
            text_entry(DisplayKind.SUCCESS, "任务已完成"),
            80,
            color=ColorMode.ALWAYS,
        )
        self.assertIn("\x1b[38;5;250m↳\x1b[0m", rendered)
        self.assertIn(
            "\x1b[38;5;250mread_file completed\x1b[0m", rendered
        )
        self.assertIn("\x1b[38;5;114m✓\x1b[0m", completed)
        self.assertIn("\x1b[38;5;114m任务已完成\x1b[0m", completed)

    def test_cjk_clipping_uses_display_columns(self) -> None:
        self.assertEqual(display_width("ab中文"), 6)
        self.assertEqual(clip_display("ab中文", 5), "ab中")

    def test_live_tail_does_not_clear_screen_or_enable_mouse_tracking(self) -> None:
        tail = render_live_tail(
            "next", "idle", 80, color=ColorMode.NEVER
        )
        self.assertIn("› next", tail)
        self.assertIn(". idle", tail)
        self.assertNotIn("[2J", tail)
        self.assertNotIn("[?100", tail)

    def test_live_tail_returns_cursor_to_the_end_of_the_input(self) -> None:
        tail = render_live_tail(
            "中文", "idle", 80, color=ColorMode.NEVER
        )
        self.assertTrue(tail.endswith("\x1b[2A\x1b[8C"))

    def test_empty_composer_has_a_bordered_placeholder_with_cursor_after_prompt(
        self,
    ) -> None:
        tail = render_live_tail("", "idle", 80, color=ColorMode.ALWAYS)
        plain = _plain(tail)
        self.assertIn("│ › 输入任务、编辑请求，或输入 / 查看命令", plain)
        self.assertIn("\x1b[38;5;247m", tail)
        self.assertNotIn("\x1b[2;", tail)
        self.assertIn(
            "╭─────────────────────────────────────────────────────────────────────────────╮",
            plain,
        )
        self.assertIn(
            "╰─────────────────────────────────────────────────────────────────────────────╯",
            plain,
        )
        self.assertTrue(tail.endswith("\x1b[2A\x1b[4C"))

    def test_multiline_composer_grows_and_tracks_the_active_row(self) -> None:
        frame = render_live_tail_frame(
            "first\nsecond", "idle", 40, color=ColorMode.NEVER
        )
        self.assertEqual(frame.geometry.height, 5)
        self.assertEqual(frame.geometry.cursor_row, 2)
        self.assertIn("│ › first", frame.text)
        self.assertIn("│   second", frame.text)

    def test_palette_is_rendered_above_the_composer(self) -> None:
        tail = render_live_tail(
            "/",
            "idle",
            80,
            color=ColorMode.NEVER,
            palette=("/帮助", "/状态"),
        )
        self.assertEqual(tail.count("\n"), 5)
        plain = _plain(tail).replace("\r", "")
        self.assertLess(plain.index("/帮助"), plain.index("╭"))
        self.assertLess(plain.index("/状态"), plain.index("╭"))
        self.assertNotIn("idle |", plain)

    def test_palette_moves_the_composer_cursor_below_its_rows(self) -> None:
        frame = render_live_tail_frame(
            "/",
            "idle",
            80,
            color=ColorMode.NEVER,
            palette=("/帮助", "/状态"),
        )
        self.assertEqual(frame.geometry.cursor_row, 3)

    def test_transcript_uses_medium_spacing_without_expanding_tool_groups(
        self,
    ) -> None:
        rendered = render_entries(
            (
                text_entry(DisplayKind.USER, "first"),
                text_entry(DisplayKind.TOOL, "read_file completed"),
                text_entry(DisplayKind.AGENT, "done"),
                text_entry(DisplayKind.USER, "next"),
            ),
            80,
            theme=Theme.SYMBOL,
            color=ColorMode.NEVER,
        )
        self.assertIn(
            "› first\n↳ read_file completed\n\n◆ done\n\n› next",
            rendered,
        )

    def test_status_context_is_right_aligned_and_hidden_when_space_is_tight(
        self,
    ) -> None:
        wide = _plain(
            render_live_tail(
                "x",
                "处理中",
                80,
                color=ColorMode.ALWAYS,
                status_context="gpt-5 · 00:18",
            )
        )
        narrow = _plain(
            render_live_tail(
                "x",
                "处理中",
                16,
                color=ColorMode.NEVER,
                status_context="gpt-5 · 00:18",
            )
        )
        self.assertIn("gpt-5 · 00:18", wide)
        self.assertNotIn("gpt-5 · 00:18", narrow)

    def test_compatibility_renderer_is_append_only(self) -> None:
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())),
            ApprovalBroker(),
        )
        app.state.entries.append(text_entry(DisplayKind.AGENT, "done"))
        rendered = render_terminal(app.state, "", 80, 24)
        self.assertIn("◆ done", rendered)
        self.assertNotIn("[2J", rendered)

    def test_markdown_heading_and_paragraphs_render_on_separate_lines(
        self,
    ) -> None:
        entry = text_entry(
            DisplayKind.AGENT,
            "说明\n\n### 功能说明\n\n- 项目",
        )
        rendered = render_entry(entry, 80, color=ColorMode.NEVER)
        self.assertIn("◆ 说明", rendered)
        self.assertIn("  功能说明", rendered)
        self.assertIn("  - 项目", rendered)
        self.assertNotIn("###", rendered)

    def test_markdown_table_uses_three_rules_without_vertical_lines(
        self,
    ) -> None:
        entry = text_entry(
            DisplayKind.AGENT,
            "| 位置 | 原来 | 现在 |\n"
            "| --- | --- | --- |\n"
            "| 函数名 | add(a, b) | multiply(a, b) |",
        )
        rendered = render_entry(entry, 44, color=ColorMode.NEVER)
        self.assertIn("─", rendered)
        self.assertIn("函数名", rendered)
        self.assertNotIn("│", rendered)
        self.assertNotIn("| ---", rendered)
        self.assertTrue(
            all(display_width(line) <= 44 for line in rendered.splitlines())
        )

    def test_markdown_table_header_and_border_use_distinct_local_colors(
        self,
    ) -> None:
        entry = text_entry(
            DisplayKind.AGENT,
            "| A | B |\n| --- | --- |\n| 1 | 2 |",
        )
        rendered = render_entry(entry, 40, color=ColorMode.ALWAYS)
        self.assertIn("\x1b[1;96m", rendered)
        self.assertIn("\x1b[38;5;247m", rendered)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Remove the moved class and now-unused imports**

In `test_windows_tui.py`, delete only `TerminalFirstRendererTests`.
Keep `_ANSI` and `_plain`, which remain used by `WindowsTerminalAppTests`.

Replace:

```python
from code_agent.interfaces.terminal_display import DisplayKind, clip_display, display_width, text_entry
from code_agent.interfaces.terminal_renderer import ColorMode, Theme, render_entries, render_entry, render_live_tail
from code_agent.interfaces.terminal_tail import render_live_tail_frame
```

with:

```python
from code_agent.interfaces.terminal_display import DisplayKind
```

Replace:

```python
from code_agent.interfaces.windows_tui import WindowsTerminalApp, render_terminal
```

with:

```python
from code_agent.interfaces.windows_tui import WindowsTerminalApp
```

- [ ] **Step 3: Verify the mechanical move**

```powershell
$python = ".venv\Scripts\python.exe"
& $python -B -m unittest discover `
  -s src/code_agent/interfaces/tests `
  -p "test_terminal_renderer.py" -v
if ($LASTEXITCODE -ne 0) {
    throw "moved renderer tests failed"
}
& $python -B -m unittest discover `
  -s src/code_agent/interfaces/tests `
  -p "test_windows_tui.py" -v
if ($LASTEXITCODE -ne 0) {
    throw "remaining windows TUI tests failed"
}
& $python -B -m unittest discover `
  -s src/code_agent/interfaces/tests `
  -p "test_*.py" -v
if ($LASTEXITCODE -ne 0) {
    throw "Interfaces suite failed after renderer split"
}
```

Expected: 18, 19, and 106 passing tests.

---

### Task 0B: Bound the persisted-thread test fixture

**Files:**

- Modify: `src/code_agent/interfaces/tests/test_terminal_state.py`

- [ ] **Step 1: Extract the exact fixture**

Insert before `TerminalStateTests`:

```python
def _persisted_thread_history() -> RestoredThread:
    return RestoredThread(
        thread_id="thread-1",
        messages=(
            Message(role="user", content="inspect"),
            Message(role="assistant", content="done"),
            Message(role="tool", name="read_file", content="raw output"),
        ),
        events=(
            AgentEvent(
                EventKind.ACTION_REQUESTED,
                {
                    "request": {
                        "id": "call-1",
                        "name": "read_file",
                        "arguments": {"diff": "--- a/x\n+++ b/x"},
                    }
                },
            ),
            AgentEvent(
                EventKind.ACTION_COMPLETED,
                {
                    "result": ActionResult(
                        request_id="call-1",
                        name="read_file",
                        output={"content": "x"},
                    ).to_dict()
                },
            ),
            AgentEvent(EventKind.COMPLETED, {"thread_id": "thread-1"}),
        ),
        goals=(
            GoalRecord(
                id="goal-1",
                thread_id="thread-1",
                objective="inspect repository",
                status=GoalStatus.ACTIVE,
            ),
        ),
        checkpoints=(
            CheckpointRecord(
                id="checkpoint-1",
                thread_id="thread-1",
                label="before edits",
                created_at=datetime(2026, 7, 11, tzinfo=timezone.utc),
            ),
        ),
    )
```

Replace the existing oversized test with:

```python
def test_restore_projects_persisted_thread_state(self) -> None:
    state = TerminalState()

    state.restore(_persisted_thread_history())

    self.assertEqual(state.thread_id, "thread-1")
    self.assertEqual(state.status, "completed")
    self.assertEqual(state.summary[0], "goal: inspect repository")
    self.assertEqual(state.transcript, ["user: inspect", "assistant: done"])
    self.assertEqual(
        state.timeline[-2:], ["requested read_file", "completed read_file"]
    )
```

- [ ] **Step 2: Verify the focused and full behavior**

```powershell
$python = ".venv\Scripts\python.exe"
& $python -B -m unittest `
  src.code_agent.interfaces.tests.test_terminal_state.TerminalStateTests.test_restore_projects_persisted_thread_state `
  -v
if ($LASTEXITCODE -ne 0) {
    throw "persisted thread test failed"
}
& $python -B -m unittest discover `
  -s src/code_agent/interfaces/tests `
  -p "test_*.py" -v
if ($LASTEXITCODE -ne 0) {
    throw "Interfaces suite failed after fixture extraction"
}
```

Expected: 1 and 106 passing tests.

---

### Task 0C: Extract existing display command dispatch

**Files:**

- Create: `src/code_agent/interfaces/tui_display_commands.py`
- Modify: `src/code_agent/interfaces/windows_tui.py`

- [ ] **Step 1: Run the display-command characterization probe**

```powershell
$python = ".venv\Scripts\python.exe"
$probe = @'
import asyncio
import sys

sys.path.insert(0, "src")

from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp

async def main():
    cases = (
        ("/language en", True, "language updated"),
        ("/language bad", False, "language must be zh-CN or en"),
        ("/theme plain", True, "theme updated"),
        ("/theme bad", False, "theme must be signal, symbol, or plain"),
        ("/color never", True, "color updated"),
        ("/color bad", False, "color must be auto, always, or never"),
        ("/glyphs ascii", True, "glyphs updated"),
        ("/glyphs unicode", True, "glyphs updated"),
        ("/glyphs bad", False, "glyphs must be ascii or unicode"),
    )
    for command, expected_result, expected_text in cases:
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())),
            ApprovalBroker(),
            write=lambda _: None,
        )
        assert await app.submit(command) is expected_result
        assert app.state.entries[-1].text == expected_text

asyncio.run(main())
print("DISPLAY COMMAND CHARACTERIZATION OK")
'@
& $python -B -c $probe
if ($LASTEXITCODE -ne 0) {
    throw "display command characterization failed"
}
```

Expected: `DISPLAY COMMAND CHARACTERIZATION OK`.

- [ ] **Step 2: Create the bounded handler**

Create `src/code_agent/interfaces/tui_display_commands.py`:

```python
from __future__ import annotations

from typing import Any

from .i18n import Language, catalog_for
from .terminal_display import DisplayKind
from .terminal_renderer import ColorMode, Theme
from .tui_commands import TuiCommand, TuiCommandKind


def _language(app: Any, value: str) -> bool:
    normalized = value.casefold()
    if normalized in {"zh", "zh-cn"}:
        app.catalog = catalog_for(Language.ZH_CN)
    elif normalized in {"en", "en-us"}:
        app.catalog = catalog_for(Language.EN_US)
    else:
        app._append(DisplayKind.ERROR, "language must be zh-CN or en")
        return False
    text = (
        "语言已切换"
        if app.catalog.language is Language.ZH_CN
        else "language updated"
    )
    app._append(DisplayKind.METADATA, text)
    return True


def _theme(app: Any, value: str) -> bool:
    try:
        app.theme = Theme(value)
    except ValueError:
        app._append(
            DisplayKind.ERROR,
            "theme must be signal, symbol, or plain",
        )
        return False
    app._append(DisplayKind.METADATA, "theme updated")
    return True


def _color(app: Any, value: str) -> bool:
    try:
        app.color = ColorMode(value)
    except ValueError:
        app._append(
            DisplayKind.ERROR,
            "color must be auto, always, or never",
        )
        return False
    app._append(DisplayKind.METADATA, "color updated")
    return True


def _glyphs(app: Any, value: str) -> bool:
    if value == "ascii":
        app.theme = Theme.SIGNAL
    elif value == "unicode":
        app.theme = Theme.SYMBOL
    else:
        app._append(
            DisplayKind.ERROR,
            "glyphs must be ascii or unicode",
        )
        return False
    app._append(DisplayKind.METADATA, "glyphs updated")
    return True


async def handle_display_command(
    app: Any,
    command: TuiCommand,
) -> bool | None:
    value = command.instruction or ""
    if command.kind is TuiCommandKind.LANGUAGE:
        return _language(app, value)
    if command.kind is TuiCommandKind.THEME:
        return _theme(app, value)
    if command.kind is TuiCommandKind.COLOR:
        return _color(app, value)
    if command.kind is TuiCommandKind.GLYPHS:
        return _glyphs(app, value)
    return None
```

- [ ] **Step 3: Delegate only those four branches**

In `windows_tui.py`, add:

```python
from .tui_display_commands import handle_display_command
```

Replace:

```python
from .i18n import Language, catalog_for, localize_task_status, select_runtime_language
```

with:

```python
from .i18n import catalog_for, localize_task_status, select_runtime_language
```

Immediately after the existing builtin handler:

```python
display = await handle_display_command(self, command)
if display is not None:
    return display
```

Delete the old `LANGUAGE` through `GLYPHS` branches and leave every other branch
in its original order.

- [ ] **Step 4: Prove behavior preservation and structural GREEN**

Rerun the characterization probe from Step 1, then:

```powershell
$python = ".venv\Scripts\python.exe"
& $python -B -m unittest discover `
  -s src/code_agent/interfaces/tests `
  -p "test_windows_tui.py" -v
if ($LASTEXITCODE -ne 0) {
    throw "windows TUI tests failed after display extraction"
}
& $python -B -m unittest discover `
  -s src/code_agent/interfaces/tests `
  -p "test_*.py" -v
if ($LASTEXITCODE -ne 0) {
    throw "Interfaces suite failed after display extraction"
}
& $python -B -m compileall -q src/code_agent/interfaces
if ($LASTEXITCODE -ne 0) {
    throw "Interfaces compileall failed after display extraction"
}
```

Expected: 19 and 106 tests pass; compilation is silent.

- [ ] **Step 5: Run the full structural probe from Task 0**

Expected: `STRUCTURE OK` and exit 0. The resulting `_handle_command` must be no
more than 50 AST lines; no file may exceed 300 physical lines.

- [ ] **Step 6: Commit the approved structural prerequisite**

```powershell
git diff --check
if ($LASTEXITCODE -ne 0) {
    throw "structural prerequisite whitespace gate failed"
}
git status --short
git add -- `
  src/code_agent/interfaces/tests/test_terminal_renderer.py `
  src/code_agent/interfaces/tests/test_windows_tui.py `
  src/code_agent/interfaces/tests/test_terminal_state.py `
  src/code_agent/interfaces/tui_display_commands.py `
  src/code_agent/interfaces/windows_tui.py
$expected = @(
  "src/code_agent/interfaces/tests/test_terminal_renderer.py"
  "src/code_agent/interfaces/tests/test_windows_tui.py"
  "src/code_agent/interfaces/tests/test_terminal_state.py"
  "src/code_agent/interfaces/tui_display_commands.py"
  "src/code_agent/interfaces/windows_tui.py"
)
$staged = @(git diff --cached --name-only)
$difference = Compare-Object `
  ($expected | Sort-Object) `
  ($staged | Sort-Object)
if ($difference) {
    throw "unexpected structural staged paths: $difference"
}
git commit -m "整理界面结构：恢复文件与函数粒度合规"
if ($LASTEXITCODE -ne 0) {
    throw "structural prerequisite commit failed"
}
$structuralBase = (& git rev-parse HEAD).Trim()
Write-Output "STRUCTURAL_BASE=$structuralBase"
git status --short
if (git status --porcelain) {
    throw "structural prerequisite commit left a dirty worktree"
}
```

Expected: only the five allowlisted files are committed and the worktree is
clean. Record the printed exact SHA in the Codex task state. The coordinator
must pass that literal SHA to every fresh Tasks 1–4 worker, which starts with
`$structuralBase = "<recorded exact SHA>"`; do not rediscover it from a mutable
branch position or commit subject.

The complete immutability probe is:

```powershell
$range = "${structuralBase}..HEAD"
git diff --exit-code $range -- `
  src/code_agent/interfaces/windows_tui.py `
  src/code_agent/interfaces/tests/test_terminal_renderer.py `
  src/code_agent/interfaces/tests/test_windows_tui.py `
  src/code_agent/interfaces/tests/test_terminal_state.py `
  src/code_agent/interfaces/tui_display_commands.py
if ($LASTEXITCODE -ne 0) {
    throw "committed structural baseline changed"
}

git diff --exit-code -- `
  src/code_agent/interfaces/windows_tui.py `
  src/code_agent/interfaces/tests/test_terminal_renderer.py `
  src/code_agent/interfaces/tests/test_windows_tui.py `
  src/code_agent/interfaces/tests/test_terminal_state.py `
  src/code_agent/interfaces/tui_display_commands.py
if ($LASTEXITCODE -ne 0) {
    throw "working structural baseline changed"
}

git diff --cached --exit-code -- `
  src/code_agent/interfaces/windows_tui.py `
  src/code_agent/interfaces/tests/test_terminal_renderer.py `
  src/code_agent/interfaces/tests/test_windows_tui.py `
  src/code_agent/interfaces/tests/test_terminal_state.py `
  src/code_agent/interfaces/tui_display_commands.py
if ($LASTEXITCODE -ne 0) {
    throw "staged structural baseline changed"
}
```

Expected before and after every rewind commit: all three commands have no output
and exit 0. The second and third commands are required before commit so an
accidental protected-file edit cannot hide outside the committed range.

---

### Task 1: Freeze rewind models and the read-only source protocol

**Files:**

- Create: `src/code_agent/interfaces/_rewind_model_validation.py`
- Create: `src/code_agent/interfaces/_rewind_candidate_models.py`
- Create: `src/code_agent/interfaces/rewind_models.py`
- Create: `src/code_agent/interfaces/tests/test_rewind_models.py`
- Create: `src/code_agent/interfaces/tests/test_rewind_candidates.py`
- Create: `src/code_agent/interfaces/tests/test_rewind_preview_models.py`

- [ ] **Step 1: Write the complete failing model tests**

Create `src/code_agent/interfaces/tests/test_rewind_models.py`:

```python
from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

from code_agent.interfaces.rewind_models import (
    RewindAsOf,
    RewindDisabledReason,
    RewindFacts,
    RewindKind,
    RewindPath,
    RewindPreview,
    RewindPreviewSource,
)


UTC_TIME = datetime(2026, 7, 19, 4, 30, tzinfo=timezone.utc)
DIGEST = "a" * 64


def as_of() -> RewindAsOf:
    return RewindAsOf(4, 5, 6, 2, DIGEST, UTC_TIME)


def path(name: str = "src/app.py") -> RewindPath:
    return RewindPath(name, "git-unstaged", True)


def preview(
    reasons: tuple[RewindDisabledReason, ...] = (),
) -> RewindPreview:
    enabled = not reasons
    return RewindPreview(
        RewindKind.BOTH,
        "checkpoint-1",
        "Before refactor",
        as_of(),
        3 if enabled else 0,
        (path(),) if enabled else (),
        reasons,
        enabled,
        enabled,
        False,
        False,
    )


class RewindEnumTests(unittest.TestCase):
    def test_enum_wire_values_and_reason_priority_are_stable(self) -> None:
        self.assertEqual(
            tuple(item.value for item in RewindKind),
            ("conversation", "code", "both"),
        )
        self.assertEqual(
            tuple(item.value for item in RewindDisabledReason),
            (
                "checkpoint-not-found",
                "message-bound-missing",
                "message-bound-invalid",
                "code-coverage-unavailable",
                "code-journal-incomplete",
                "pending-workspace-mutation",
                "snapshot-missing",
                "snapshot-invalid",
                "workspace-conflict",
                "preview-limit-exceeded",
                "source-changed-during-preview",
            ),
        )


class RewindAsOfTests(unittest.TestCase):
    def test_aware_times_are_normalized_to_utc_and_record_is_frozen(self) -> None:
        local = datetime(
            2026,
            7,
            19,
            12,
            30,
            tzinfo=timezone(timedelta(hours=8)),
        )
        value = RewindAsOf(None, 0, 2, 1, None, local)
        self.assertEqual(value.captured_at, UTC_TIME)
        self.assertIs(value.captured_at.tzinfo, timezone.utc)
        with self.assertRaises(FrozenInstanceError):
            value.message_sequence = 3  # type: ignore[misc]

    def test_sequences_reject_bool_negative_and_non_integer_values(self) -> None:
        for value in (True, -1, 1.5, "1"):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    RewindAsOf(  # type: ignore[arg-type]
                        value, None, None, None, None, UTC_TIME
                    )

    def test_generation_digest_and_time_validation_fail_closed(self) -> None:
        for generation in (True, 0, -1, 1.5):
            with self.subTest(generation=generation):
                with self.assertRaises((TypeError, ValueError)):
                    RewindAsOf(  # type: ignore[arg-type]
                        None, None, None, generation, None, UTC_TIME
                    )
        for digest in ("A" * 64, "a" * 63, "g" * 64, 3):
            with self.subTest(digest=digest):
                with self.assertRaises((TypeError, ValueError)):
                    RewindAsOf(  # type: ignore[arg-type]
                        None, None, None, None, digest, UTC_TIME
                    )
        with self.assertRaises(ValueError):
            RewindAsOf(
                None,
                None,
                None,
                None,
                None,
                datetime(2026, 7, 19),
            )


class RewindPathTests(unittest.TestCase):
    def test_canonical_relative_posix_paths_are_accepted(self) -> None:
        value = RewindPath(
            "src/code agent/app.py",
            "git-untracked",
            True,
        )
        self.assertEqual(value.path, "src/code agent/app.py")

    def test_noncanonical_or_unsafe_paths_are_rejected(self) -> None:
        invalid = (
            "",
            "/absolute.py",
            "C:/absolute.py",
            "src\\app.py",
            "src//app.py",
            "./app.py",
            "src/../app.py",
            "src/\x00app.py",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    RewindPath(value, "git-unstaged", True)

    def test_bounded_text_and_boolean_fields_are_strict(self) -> None:
        for value in ("", " ", "x" * 513):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    RewindPath("a.py", value, True)
        with self.assertRaises(TypeError):
            RewindPath("a.py", "absent", 1)  # type: ignore[arg-type]


class RewindFactAndPreviewTests(unittest.TestCase):
    def test_fact_tuple_fields_reject_mutable_wrong_and_duplicate_paths(
        self,
    ) -> None:
        base = dict(
            checkpoint_id="checkpoint-1",
            checkpoint_label="Before refactor",
            checkpoint_created_at=UTC_TIME,
            as_of=as_of(),
            conversation_messages=2,
            conversation_disabled_reason=None,
            code_disabled_reason=None,
        )
        for paths in ([path()], ("not-a-path",), (path(), path())):
            with self.subTest(paths=paths):
                with self.assertRaises((TypeError, ValueError)):
                    RewindFacts(  # type: ignore[arg-type]
                        code_paths=paths,
                        **base,
                    )

    def test_disabled_facets_cannot_retain_partial_results(self) -> None:
        common = dict(
            checkpoint_id="checkpoint-1",
            checkpoint_label="Before refactor",
            checkpoint_created_at=UTC_TIME,
            as_of=as_of(),
        )
        with self.assertRaises(ValueError):
            RewindFacts(
                conversation_messages=1,
                code_paths=(),
                conversation_disabled_reason=(
                    RewindDisabledReason.MESSAGE_BOUND_MISSING
                ),
                code_disabled_reason=None,
                **common,
            )
        with self.assertRaises(ValueError):
            RewindFacts(
                conversation_messages=0,
                code_paths=(path(),),
                conversation_disabled_reason=None,
                code_disabled_reason=(
                    RewindDisabledReason.CODE_JOURNAL_INCOMPLETE
                ),
                **common,
            )

    def test_counts_reject_bool_negative_and_non_integer_values(self) -> None:
        for count in (True, -1, 1.5):
            with self.subTest(count=count):
                with self.assertRaises((TypeError, ValueError)):
                    RewindFacts(
                        "checkpoint-1",
                        "Before refactor",
                        UTC_TIME,
                        as_of(),
                        count,  # type: ignore[arg-type]
                        (),
                        None,
                        None,
                    )

    def test_preview_invariants_are_enforced(self) -> None:
        value = preview()
        self.assertTrue(value.enabled)
        self.assertTrue(value.requires_confirmation)
        self.assertFalse(value.apply_available)
        self.assertFalse(value.requires_git_reset)
        with self.assertRaises(ValueError):
            RewindPreview(
                RewindKind.BOTH,
                "checkpoint-1",
                "Before refactor",
                as_of(),
                0,
                (),
                (),
                False,
                False,
                False,
                False,
            )
        with self.assertRaises(ValueError):
            RewindPreview(
                RewindKind.BOTH,
                "checkpoint-1",
                "Before refactor",
                as_of(),
                0,
                (),
                (RewindDisabledReason.WORKSPACE_CONFLICT,),
                False,
                False,
                True,
                False,
            )

    def test_disabled_reasons_must_be_unique_and_in_priority_order(self) -> None:
        conflict = RewindDisabledReason.WORKSPACE_CONFLICT
        missing = RewindDisabledReason.MESSAGE_BOUND_MISSING
        for reasons in ((conflict, conflict), (conflict, missing)):
            with self.subTest(reasons=reasons):
                with self.assertRaises(ValueError):
                    preview(reasons)


if __name__ == "__main__":
    unittest.main()
```

Create `src/code_agent/interfaces/tests/test_rewind_candidates.py`:

```python
from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

from code_agent.interfaces.rewind_models import (
    RewindCheckpointCandidate,
    RewindCheckpointPage,
    RewindPreviewSource,
)


UTC_TIME = datetime(2026, 7, 19, 4, 30, tzinfo=timezone.utc)


def candidate(identifier: str) -> RewindCheckpointCandidate:
    return RewindCheckpointCandidate(
        identifier,
        "Before refactor",
        UTC_TIME,
        True,
        False,
    )


class RewindCandidateTests(unittest.TestCase):
    def test_candidate_metadata_is_bounded_and_normalized_to_utc(self) -> None:
        local = datetime(
            2026,
            7,
            19,
            12,
            30,
            tzinfo=timezone(timedelta(hours=8)),
        )
        value = RewindCheckpointCandidate(
            "c1", "label", local, True, False
        )
        self.assertEqual(value.created_at, UTC_TIME)
        invalid = (
            ("", "label", UTC_TIME),
            ("c1", "x" * 513, UTC_TIME),
            ("c1", "label", datetime(2026, 7, 19)),
        )
        for identifier, label, created_at in invalid:
            with self.subTest(identifier=identifier, label=label):
                with self.assertRaises((TypeError, ValueError)):
                    RewindCheckpointCandidate(
                        identifier,
                        label,
                        created_at,
                        True,
                        False,
                    )

    def test_page_is_frozen_and_keeps_opaque_cursor(self) -> None:
        page = RewindCheckpointPage(
            (candidate("c1"),),
            "opaque:+/% cursor",
        )
        self.assertEqual(page.next_cursor, "opaque:+/% cursor")
        with self.assertRaises(FrozenInstanceError):
            page.next_cursor = None  # type: ignore[misc]

    def test_page_rejects_mutable_wrong_duplicate_and_oversized_items(
        self,
    ) -> None:
        item = candidate("c1")
        invalid = ([item], ("candidate",), (item, item))
        for items in invalid:
            with self.subTest(items=items):
                with self.assertRaises((TypeError, ValueError)):
                    RewindCheckpointPage(  # type: ignore[arg-type]
                        items,
                        None,
                    )
        with self.assertRaises(ValueError):
            RewindCheckpointPage(
                tuple(candidate(f"c{index}") for index in range(101)),
                None,
            )

    def test_cursor_is_opaque_but_bounded(self) -> None:
        for cursor in ("", " ", "opaque:+/% cursor"):
            with self.subTest(cursor=cursor):
                page = RewindCheckpointPage((), cursor)
                self.assertEqual(page.next_cursor, cursor)
        for cursor in ("x" * 1025,):
            with self.subTest(cursor=cursor):
                with self.assertRaises(ValueError):
                    RewindCheckpointPage((), cursor)
        with self.assertRaises(TypeError):
            RewindCheckpointPage((), 1)  # type: ignore[arg-type]

    def test_candidate_boolean_fields_are_strict(self) -> None:
        for message_bound, code_anchor in ((1, False), (True, 0)):
            with self.subTest(
                message_bound=message_bound,
                code_anchor=code_anchor,
            ):
                with self.assertRaises(TypeError):
                    RewindCheckpointCandidate(  # type: ignore[arg-type]
                        "c1",
                        "label",
                        UTC_TIME,
                        message_bound,
                        code_anchor,
                    )

    def test_source_protocol_contains_only_read_methods(self) -> None:
        public = {
            name
            for name in RewindPreviewSource.__dict__
            if not name.startswith("_")
        }
        self.assertEqual(public, {"list_candidates", "preview"})
        self.assertNotIn("apply", public)
        self.assertNotIn("restore", public)
        self.assertNotIn("confirm", public)


if __name__ == "__main__":
    unittest.main()
```

Create `src/code_agent/interfaces/tests/test_rewind_preview_models.py`:

```python
from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

from code_agent.interfaces.rewind_models import (
    RewindAsOf,
    RewindCheckpointCandidate,
    RewindCheckpointPage,
    RewindDisabledReason,
    RewindFacts,
    RewindKind,
    RewindPath,
    RewindPreview,
)


UTC_TIME = datetime(2026, 7, 19, 4, 30, tzinfo=timezone.utc)


def preview(
    kind: RewindKind,
    reasons: tuple[RewindDisabledReason, ...],
    *,
    messages: int = 0,
    paths: tuple[RewindPath, ...] = (),
) -> RewindPreview:
    return RewindPreview(
        kind,
        "checkpoint-1",
        "Before refactor",
        RewindAsOf(None, None, None, None, None, UTC_TIME),
        messages,
        paths,
        reasons,
        False,
        False,
        False,
        False,
    )


class RewindPreviewFacetTests(unittest.TestCase):
    def test_all_public_records_are_frozen(self) -> None:
        observation = RewindAsOf(
            None, None, None, None, None, UTC_TIME
        )
        rewind_path = RewindPath("src/app.py", "git-unstaged", True)
        candidate = RewindCheckpointCandidate(
            "c1", "Before refactor", UTC_TIME, True, True
        )
        records = (
            observation,
            rewind_path,
            RewindFacts(
                "c1",
                "Before refactor",
                UTC_TIME,
                observation,
                0,
                (),
                None,
                None,
            ),
            preview(
                RewindKind.CODE,
                (RewindDisabledReason.WORKSPACE_CONFLICT,),
            ),
            candidate,
            RewindCheckpointPage((candidate,), None),
        )
        for record in records:
            with self.subTest(record=type(record).__name__):
                field = next(iter(record.__dataclass_fields__))
                with self.assertRaises(FrozenInstanceError):
                    setattr(record, field, None)

    def test_facts_reject_reasons_for_the_wrong_facet(self) -> None:
        common = dict(
            checkpoint_id="checkpoint-1",
            checkpoint_label="Before refactor",
            checkpoint_created_at=UTC_TIME,
            as_of=RewindAsOf(
                None, None, None, None, None, UTC_TIME
            ),
            conversation_messages=0,
            code_paths=(),
        )
        cases = (
            (
                RewindDisabledReason.WORKSPACE_CONFLICT,
                None,
            ),
            (
                None,
                RewindDisabledReason.MESSAGE_BOUND_INVALID,
            ),
        )
        for conversation_reason, code_reason in cases:
            with self.subTest(
                conversation_reason=conversation_reason,
                code_reason=code_reason,
            ):
                with self.assertRaises(ValueError):
                    RewindFacts(
                        conversation_disabled_reason=conversation_reason,
                        code_disabled_reason=code_reason,
                        **common,
                    )

    def test_both_rejects_partial_conversation_result(self) -> None:
        with self.assertRaises(ValueError):
            preview(
                RewindKind.BOTH,
                (RewindDisabledReason.MESSAGE_BOUND_MISSING,),
                messages=1,
            )

    def test_both_rejects_partial_code_result(self) -> None:
        with self.assertRaises(ValueError):
            preview(
                RewindKind.BOTH,
                (RewindDisabledReason.WORKSPACE_CONFLICT,),
                paths=(RewindPath("src/app.py", "git-unstaged", True),),
            )

    def test_global_reason_rejects_both_partial_facets(self) -> None:
        reason = RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW
        with self.assertRaises(ValueError):
            preview(RewindKind.BOTH, (reason,), messages=1)
        with self.assertRaises(ValueError):
            preview(
                RewindKind.BOTH,
                (reason,),
                paths=(RewindPath("src/app.py", "git-unstaged", True),),
            )

    def test_code_preview_limit_preserves_valid_conversation_count(self) -> None:
        value = preview(
            RewindKind.BOTH,
            (RewindDisabledReason.PREVIEW_LIMIT_EXCEEDED,),
            messages=2,
        )
        self.assertEqual(value.conversation_messages, 2)
        self.assertFalse(value.enabled)

    def test_kind_rejects_inapplicable_facet_reasons(self) -> None:
        cases = (
            (
                RewindKind.CONVERSATION,
                RewindDisabledReason.CODE_JOURNAL_INCOMPLETE,
            ),
            (
                RewindKind.CODE,
                RewindDisabledReason.MESSAGE_BOUND_INVALID,
            ),
        )
        for kind, reason in cases:
            with self.subTest(kind=kind, reason=reason):
                with self.assertRaises(ValueError):
                    preview(kind, (reason,))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused test to verify RED**

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest `
  src.code_agent.interfaces.tests.test_rewind_models `
  src.code_agent.interfaces.tests.test_rewind_candidates `
  src.code_agent.interfaces.tests.test_rewind_preview_models -v
```

Expected: `ModuleNotFoundError` for
`code_agent.interfaces.rewind_models`.

- [ ] **Step 3: Implement bounded private validation helpers**

Create `src/code_agent/interfaces/_rewind_model_validation.py`:

```python
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TypeVar


T = TypeVar("T")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def bounded_text(
    value: object,
    name: str,
    *,
    max_length: int = 512,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    if not value.strip() or len(value) > max_length:
        raise ValueError(
            f"{name} must be non-blank text of at most "
            f"{max_length} characters"
        )
    return value


def optional_text(
    value: object,
    name: str,
    *,
    max_length: int = 512,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text or None")
    if len(value) > max_length:
        raise ValueError(
            f"{name} must be at most {max_length} characters"
        )
    return value


def optional_exact(
    value: object,
    item_type: type[T],
    name: str,
) -> T | None:
    if value is not None and type(value) is not item_type:
        raise TypeError(
            f"{name} must be an exact {item_type.__name__} or None"
        )
    return value


def optional_integer(
    value: object,
    name: str,
    *,
    positive: bool = False,
) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer or None")
    minimum = 1 if positive else 0
    if value < minimum:
        raise ValueError(f"{name} is below its minimum")
    return value


def count(value: object, name: str) -> int:
    result = optional_integer(value, name)
    if result is None:
        raise TypeError(f"{name} must be an integer")
    return result


def utc(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return value.astimezone(timezone.utc)


def digest(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("relevant_path_digest must be text or None")
    if _SHA256.fullmatch(value) is None:
        raise ValueError(
            "relevant_path_digest must be lowercase SHA-256"
        )
    return value


def path_text(value: object) -> str:
    path = bounded_text(value, "path")
    parts = path.split("/")
    invalid = (
        path.startswith("/")
        or parts[0].endswith(":")
        or "\\" in path
        or "\x00" in path
        or any(part in {"", ".", ".."} for part in parts)
    )
    if invalid:
        raise ValueError(
            "path must be a canonical relative POSIX path"
        )
    return path


def exact_tuple(
    value: object,
    item_type: type[T],
    name: str,
) -> tuple[T, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{name} must be a tuple")
    if any(type(item) is not item_type for item in value):
        raise TypeError(
            f"{name} must contain exact {item_type.__name__} values"
        )
    return tuple(value)


def ordered_unique_tuple(
    value: object,
    item_type: type[T],
    name: str,
    priority: dict[T, int],
) -> tuple[T, ...]:
    items = exact_tuple(value, item_type, name)
    ordered = tuple(
        sorted(set(items), key=priority.__getitem__)
    )
    if items != ordered:
        raise ValueError(
            f"{name} must be unique and priority ordered"
        )
    return items
```

- [ ] **Step 4: Implement the isolated candidate records**

Create `src/code_agent/interfaces/_rewind_candidate_models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ._rewind_model_validation import (
    bounded_text,
    exact_tuple,
    optional_text,
    utc,
)


@dataclass(frozen=True)
class RewindCheckpointCandidate:
    checkpoint_id: str
    label: str
    created_at: datetime
    has_message_bound: bool
    has_code_anchor: bool

    def __post_init__(self) -> None:
        bounded_text(self.checkpoint_id, "checkpoint_id")
        bounded_text(self.label, "label")
        object.__setattr__(
            self,
            "created_at",
            utc(self.created_at, "created_at"),
        )
        if type(self.has_message_bound) is not bool:
            raise TypeError("has_message_bound must be boolean")
        if type(self.has_code_anchor) is not bool:
            raise TypeError("has_code_anchor must be boolean")


@dataclass(frozen=True)
class RewindCheckpointPage:
    items: tuple[RewindCheckpointCandidate, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        exact_tuple(
            self.items,
            RewindCheckpointCandidate,
            "items",
        )
        if len(self.items) > 100:
            raise ValueError(
                "candidate page cannot exceed 100 items"
            )
        identifiers = tuple(
            item.checkpoint_id for item in self.items
        )
        if len(set(identifiers)) != len(identifiers):
            raise ValueError(
                "candidate identifiers must be unique"
            )
        optional_text(
            self.next_cursor,
            "next_cursor",
            max_length=1024,
        )
```

- [ ] **Step 5: Implement the complete immutable models**

Create `src/code_agent/interfaces/rewind_models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from ._rewind_candidate_models import (
    RewindCheckpointCandidate,
    RewindCheckpointPage,
)
from ._rewind_model_validation import (
    bounded_text,
    count,
    digest,
    exact_tuple,
    optional_exact,
    optional_integer,
    ordered_unique_tuple,
    path_text,
    utc,
)


class RewindKind(str, Enum):
    CONVERSATION = "conversation"
    CODE = "code"
    BOTH = "both"


class RewindDisabledReason(str, Enum):
    CHECKPOINT_NOT_FOUND = "checkpoint-not-found"
    MESSAGE_BOUND_MISSING = "message-bound-missing"
    MESSAGE_BOUND_INVALID = "message-bound-invalid"
    CODE_COVERAGE_UNAVAILABLE = "code-coverage-unavailable"
    CODE_JOURNAL_INCOMPLETE = "code-journal-incomplete"
    PENDING_WORKSPACE_MUTATION = "pending-workspace-mutation"
    SNAPSHOT_MISSING = "snapshot-missing"
    SNAPSHOT_INVALID = "snapshot-invalid"
    WORKSPACE_CONFLICT = "workspace-conflict"
    PREVIEW_LIMIT_EXCEEDED = "preview-limit-exceeded"
    SOURCE_CHANGED_DURING_PREVIEW = "source-changed-during-preview"


_REASON_PRIORITY = {
    reason: index for index, reason in enumerate(RewindDisabledReason)
}
_REASONS = tuple(RewindDisabledReason)
_ALLOWED_REASONS = {
    RewindKind.CONVERSATION: frozenset(
        (*_REASONS[:3], _REASONS[-1])
    ),
    RewindKind.CODE: frozenset(
        (_REASONS[0], *_REASONS[3:])
    ),
    RewindKind.BOTH: frozenset(RewindDisabledReason),
}


def _paths(value: object) -> tuple[RewindPath, ...]:
    paths = exact_tuple(value, RewindPath, "code_paths")
    if len({item.path for item in paths}) != len(paths):
        raise ValueError("code paths must be unique")
    return paths


def _reasons(value: object) -> tuple[RewindDisabledReason, ...]:
    return ordered_unique_tuple(
        value,
        RewindDisabledReason,
        "disabled_reasons",
        _REASON_PRIORITY,
    )


@dataclass(frozen=True)
class RewindAsOf:
    message_sequence: int | None
    event_sequence: int | None
    mutation_sequence: int | None
    coverage_generation: int | None
    relevant_path_digest: str | None
    captured_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "message_sequence",
            "event_sequence",
            "mutation_sequence",
        ):
            optional_integer(getattr(self, name), name)
        optional_integer(
            self.coverage_generation,
            "coverage_generation",
            positive=True,
        )
        digest(self.relevant_path_digest)
        object.__setattr__(
            self,
            "captured_at",
            utc(self.captured_at, "captured_at"),
        )


@dataclass(frozen=True)
class RewindPath:
    path: str
    baseline_provenance: str
    preserves_pre_agent_baseline: bool

    def __post_init__(self) -> None:
        path_text(self.path)
        bounded_text(
            self.baseline_provenance,
            "baseline_provenance",
        )
        if type(self.preserves_pre_agent_baseline) is not bool:
            raise TypeError(
                "preserves_pre_agent_baseline must be boolean"
            )


def _validate_fact_facets(
    conversation_reason: RewindDisabledReason | None,
    code_reason: RewindDisabledReason | None,
    messages: int,
    paths: tuple[RewindPath, ...],
) -> None:
    optional_exact(
        conversation_reason,
        RewindDisabledReason,
        "conversation_disabled_reason",
    )
    optional_exact(
        code_reason,
        RewindDisabledReason,
        "code_disabled_reason",
    )
    if (
        conversation_reason is not None
        and conversation_reason
        not in _ALLOWED_REASONS[RewindKind.CONVERSATION]
    ):
        raise ValueError("reason does not apply to conversation")
    if (
        code_reason is not None
        and code_reason not in _ALLOWED_REASONS[RewindKind.CODE]
    ):
        raise ValueError("reason does not apply to code")
    if conversation_reason is not None and messages != 0:
        raise ValueError(
            "disabled conversation facts must have zero messages"
        )
    if code_reason is not None and paths:
        raise ValueError(
            "disabled code facts must not contain partial paths"
        )


@dataclass(frozen=True)
class RewindFacts:
    checkpoint_id: str
    checkpoint_label: str
    checkpoint_created_at: datetime
    as_of: RewindAsOf
    conversation_messages: int
    code_paths: tuple[RewindPath, ...]
    conversation_disabled_reason: RewindDisabledReason | None
    code_disabled_reason: RewindDisabledReason | None

    def __post_init__(self) -> None:
        bounded_text(self.checkpoint_id, "checkpoint_id")
        bounded_text(self.checkpoint_label, "checkpoint_label")
        object.__setattr__(
            self,
            "checkpoint_created_at",
            utc(
                self.checkpoint_created_at,
                "checkpoint_created_at",
            ),
        )
        if type(self.as_of) is not RewindAsOf:
            raise TypeError("as_of must be a RewindAsOf")
        count(
            self.conversation_messages,
            "conversation_messages",
        )
        _paths(self.code_paths)
        _validate_fact_facets(
            self.conversation_disabled_reason,
            self.code_disabled_reason,
            self.conversation_messages,
            self.code_paths,
        )


def _validate_preview_facets(
    kind: RewindKind,
    reasons: tuple[RewindDisabledReason, ...],
    messages: int,
    paths: tuple[RewindPath, ...],
) -> None:
    reason_set = frozenset(reasons)
    allowed = _ALLOWED_REASONS[kind]
    if not reason_set.issubset(allowed):
        raise ValueError("disabled reason does not apply to kind")
    conversation_reasons = _ALLOWED_REASONS[RewindKind.CONVERSATION]
    code_reasons = _ALLOWED_REASONS[RewindKind.CODE]
    if reason_set & conversation_reasons and messages:
        raise ValueError(
            "disabled conversation preview must have zero messages"
        )
    if reason_set & code_reasons and paths:
        raise ValueError(
            "disabled code preview must not contain partial paths"
        )


@dataclass(frozen=True)
class RewindPreview:
    kind: RewindKind
    checkpoint_id: str
    checkpoint_label: str
    as_of: RewindAsOf
    conversation_messages: int
    code_paths: tuple[RewindPath, ...]
    disabled_reasons: tuple[RewindDisabledReason, ...]
    enabled: bool
    requires_confirmation: bool
    apply_available: bool
    requires_git_reset: bool

    def __post_init__(self) -> None:
        if type(self.kind) is not RewindKind:
            raise TypeError("kind must be a RewindKind")
        bounded_text(self.checkpoint_id, "checkpoint_id")
        bounded_text(self.checkpoint_label, "checkpoint_label")
        if type(self.as_of) is not RewindAsOf:
            raise TypeError("as_of must be a RewindAsOf")
        count(
            self.conversation_messages,
            "conversation_messages",
        )
        _paths(self.code_paths)
        reasons = _reasons(self.disabled_reasons)
        flags = (
            self.enabled,
            self.requires_confirmation,
            self.apply_available,
            self.requires_git_reset,
        )
        if any(type(flag) is not bool for flag in flags):
            raise TypeError("preview flags must be boolean")
        expected_enabled = not reasons
        if self.enabled is not expected_enabled:
            raise ValueError(
                "enabled must equal not disabled_reasons"
            )
        if self.requires_confirmation is not expected_enabled:
            raise ValueError(
                "confirmation is required exactly when enabled"
            )
        if self.apply_available or self.requires_git_reset:
            raise ValueError(
                "rewind preview cannot apply or require git reset"
            )
        _validate_preview_facets(
            self.kind,
            reasons,
            self.conversation_messages,
            self.code_paths,
        )


class RewindPreviewSource(Protocol):
    async def list_candidates(
        self,
        thread_id: str,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> RewindCheckpointPage:
        """Return one bounded page of checkpoint candidates."""

    async def preview(
        self,
        thread_id: str,
        checkpoint_id: str,
        kind: RewindKind,
    ) -> RewindPreview:
        """Build a read-only preview without mutating any source."""
```

- [ ] **Step 6: Run model GREEN and structural checks**

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest `
  src.code_agent.interfaces.tests.test_rewind_models `
  src.code_agent.interfaces.tests.test_rewind_candidates `
  src.code_agent.interfaces.tests.test_rewind_preview_models -v
if ($LASTEXITCODE -ne 0) {
    throw "rewind model tests failed"
}
& $python -m compileall -q `
  src/code_agent/interfaces/_rewind_model_validation.py `
  src/code_agent/interfaces/_rewind_candidate_models.py `
  src/code_agent/interfaces/rewind_models.py `
  src/code_agent/interfaces/tests/test_rewind_models.py `
  src/code_agent/interfaces/tests/test_rewind_candidates.py `
  src/code_agent/interfaces/tests/test_rewind_preview_models.py
if ($LASTEXITCODE -ne 0) {
    throw "rewind model compileall failed"
}
```

Rerun the complete Interfaces structural probe from Task 0,
`git diff --check`, and the complete immutability probe from Task 0C.

Expected: focused tests, compilation, AST gate and whitespace gate pass.

- [ ] **Step 7: Commit the model boundary**

```powershell
git add -- `
  src/code_agent/interfaces/_rewind_model_validation.py `
  src/code_agent/interfaces/_rewind_candidate_models.py `
  src/code_agent/interfaces/rewind_models.py `
  src/code_agent/interfaces/tests/test_rewind_models.py `
  src/code_agent/interfaces/tests/test_rewind_candidates.py `
  src/code_agent/interfaces/tests/test_rewind_preview_models.py
$expected = @(
  "src/code_agent/interfaces/_rewind_model_validation.py"
  "src/code_agent/interfaces/_rewind_candidate_models.py"
  "src/code_agent/interfaces/rewind_models.py"
  "src/code_agent/interfaces/tests/test_rewind_models.py"
  "src/code_agent/interfaces/tests/test_rewind_candidates.py"
  "src/code_agent/interfaces/tests/test_rewind_preview_models.py"
)
$staged = @(git diff --cached --name-only)
$difference = Compare-Object `
  ($expected | Sort-Object) `
  ($staged | Sort-Object)
if ($difference) {
    throw "unexpected model staged paths: $difference"
}
git commit -m "定义界面回溯模型：固化只读契约"
if ($LASTEXITCODE -ne 0) {
    throw "rewind model commit failed"
}
git status --short
if (git status --porcelain) {
    throw "rewind model commit left a dirty worktree"
}
```

Expected: clean worktree.

---

### Task 2: Build pure preview projection and bounded safe rendering

**Files:**

- Create: `src/code_agent/interfaces/rewind_view.py`
- Create: `src/code_agent/interfaces/tests/test_rewind_view.py`
- Create: `src/code_agent/interfaces/tests/test_rewind_candidate_view.py`

- [ ] **Step 1: Write the complete failing view tests**

Create `src/code_agent/interfaces/tests/test_rewind_view.py`:

```python
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from code_agent.interfaces.rewind_models import (
    RewindAsOf,
    RewindDisabledReason,
    RewindFacts,
    RewindKind,
    RewindPath,
)
from code_agent.interfaces.rewind_view import (
    build_rewind_preview,
    render_rewind_preview,
)


UTC_TIME = datetime(2026, 7, 19, 4, 30, tzinfo=timezone.utc)
DIGEST = "b" * 64


def facts(
    *,
    messages: int = 3,
    paths: tuple[RewindPath, ...] = (),
    conversation_reason: RewindDisabledReason | None = None,
    code_reason: RewindDisabledReason | None = None,
    checkpoint_id: str = "checkpoint-1",
    label: str = "Before refactor",
    as_of: RewindAsOf | None = None,
) -> RewindFacts:
    return RewindFacts(
        checkpoint_id,
        label,
        UTC_TIME,
        as_of or RewindAsOf(4, 5, 6, 2, DIGEST, UTC_TIME),
        messages,
        paths,
        conversation_reason,
        code_reason,
    )


def rewind_path(
    name: str,
    provenance: str = "git-unstaged",
    preserved: bool = True,
) -> RewindPath:
    return RewindPath(name, provenance, preserved)


class RewindProjectionTests(unittest.TestCase):
    def test_conversation_failure_does_not_disable_valid_code(self) -> None:
        source = facts(
            messages=0,
            paths=(rewind_path("src/app.py"),),
            conversation_reason=(
                RewindDisabledReason.MESSAGE_BOUND_MISSING
            ),
        )
        conversation = build_rewind_preview(
            RewindKind.CONVERSATION,
            source,
        )
        code = build_rewind_preview(RewindKind.CODE, source)
        both = build_rewind_preview(RewindKind.BOTH, source)
        self.assertFalse(conversation.enabled)
        self.assertTrue(code.enabled)
        self.assertEqual(code.code_paths, source.code_paths)
        self.assertFalse(both.enabled)
        self.assertEqual(
            both.disabled_reasons,
            (RewindDisabledReason.MESSAGE_BOUND_MISSING,),
        )

    def test_code_failure_does_not_disable_valid_conversation(self) -> None:
        source = facts(
            code_reason=RewindDisabledReason.WORKSPACE_CONFLICT
        )
        conversation = build_rewind_preview(
            RewindKind.CONVERSATION,
            source,
        )
        code = build_rewind_preview(RewindKind.CODE, source)
        both = build_rewind_preview(RewindKind.BOTH, source)
        self.assertTrue(conversation.enabled)
        self.assertEqual(conversation.conversation_messages, 3)
        self.assertFalse(code.enabled)
        self.assertEqual(code.code_paths, ())
        self.assertFalse(both.enabled)

    def test_both_deduplicates_and_priority_sorts_reasons(self) -> None:
        duplicate = facts(
            messages=0,
            conversation_reason=(
                RewindDisabledReason.CHECKPOINT_NOT_FOUND
            ),
            code_reason=RewindDisabledReason.CHECKPOINT_NOT_FOUND,
        )
        deduplicated = build_rewind_preview(
            RewindKind.BOTH,
            duplicate,
        )
        self.assertEqual(
            deduplicated.disabled_reasons,
            (RewindDisabledReason.CHECKPOINT_NOT_FOUND,),
        )
        reverse = facts(
            messages=0,
            conversation_reason=(
                RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW
            ),
            code_reason=RewindDisabledReason.WORKSPACE_CONFLICT,
        )
        ordered = build_rewind_preview(RewindKind.BOTH, reverse)
        self.assertEqual(
            ordered.disabled_reasons,
            (
                RewindDisabledReason.WORKSPACE_CONFLICT,
                RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW,
            ),
        )

    def test_complete_zero_change_facts_remain_enabled(self) -> None:
        source = facts(messages=0, paths=())
        for kind in RewindKind:
            with self.subTest(kind=kind):
                preview = build_rewind_preview(kind, source)
                self.assertTrue(preview.enabled)
                self.assertTrue(preview.requires_confirmation)
                self.assertFalse(preview.apply_available)
                self.assertFalse(preview.requires_git_reset)

    def test_projection_rejects_wrong_model_types(self) -> None:
        with self.assertRaises(TypeError):
            build_rewind_preview(  # type: ignore[arg-type]
                "both",
                facts(),
            )
        with self.assertRaises(TypeError):
            build_rewind_preview(  # type: ignore[arg-type]
                RewindKind.BOTH,
                object(),
            )


class RewindPreviewRenderTests(unittest.TestCase):
    def test_preview_renders_fixed_contract_and_as_of_values(self) -> None:
        preview = build_rewind_preview(
            RewindKind.BOTH,
            facts(paths=(rewind_path("src/app.py"),)),
        )
        text = render_rewind_preview(preview)
        self.assertIn("rewind · preview only", text)
        self.assertIn(
            "checkpoint: checkpoint-1 · Before refactor",
            text,
        )
        self.assertIn("kind: both", text)
        self.assertIn(
            "message=4 · event=5 · mutation=6 · coverage=2",
            text,
        )
        self.assertIn(f"paths={DIGEST}", text)
        self.assertIn("conversation messages: 3", text)
        self.assertIn("code paths: 1", text)
        self.assertIn("state: enabled", text)
        self.assertIn("confirmation required: yes", text)
        self.assertTrue(
            text.endswith("apply unavailable\nno git reset")
        )

    def test_none_observations_render_as_dash_not_zero(self) -> None:
        observation = RewindAsOf(
            None, None, None, None, None, UTC_TIME
        )
        preview = build_rewind_preview(
            RewindKind.CONVERSATION,
            facts(messages=0, as_of=observation),
        )
        text = render_rewind_preview(preview)
        self.assertIn(
            "message=- · event=- · mutation=- · "
            "coverage=- · paths=-",
            text,
        )
        self.assertNotIn("message=0", text)

    def test_disabled_conversation_is_unavailable_not_trusted_zero(
        self,
    ) -> None:
        preview = build_rewind_preview(
            RewindKind.CONVERSATION,
            facts(
                messages=0,
                conversation_reason=(
                    RewindDisabledReason.MESSAGE_BOUND_INVALID
                ),
            ),
        )
        text = render_rewind_preview(preview)
        self.assertIn("conversation messages: unavailable", text)
        self.assertIn(
            "state: disabled · message-bound-invalid",
            text,
        )
        self.assertIn("confirmation required: no", text)

    def test_global_failure_never_renders_trusted_zero_messages(self) -> None:
        preview = build_rewind_preview(
            RewindKind.CONVERSATION,
            facts(
                messages=0,
                conversation_reason=(
                    RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW
                ),
            ),
        )

        text = render_rewind_preview(preview)

        self.assertIn("conversation messages: unavailable", text)

    def test_code_preview_limit_keeps_conversation_count_visible(self) -> None:
        preview = build_rewind_preview(
            RewindKind.BOTH,
            facts(
                messages=2,
                code_reason=(
                    RewindDisabledReason.PREVIEW_LIMIT_EXCEEDED
                ),
            ),
        )

        text = render_rewind_preview(preview)

        self.assertIn("conversation messages: 2", text)
        self.assertIn("state: disabled · preview-limit-exceeded", text)

    def test_untrusted_values_are_safe_single_line_text(self) -> None:
        preview = build_rewind_preview(
            RewindKind.CODE,
            facts(
                messages=0,
                checkpoint_id="checkpoint\x1b[31m",
                label="first\u2028state: enabled",
                paths=(
                    rewind_path(
                        "src/line\nstate.py",
                        "git\u2029apply available",
                    ),
                ),
            ),
        )
        text = render_rewind_preview(preview, max_path_rows=1)
        self.assertNotIn("\x1b", text)
        self.assertEqual(text.count("state: enabled"), 2)
        self.assertNotIn("\napply available", text)
        self.assertIn("src/line state.py", text)

    def test_path_limit_uses_original_tuple_for_hidden_count(self) -> None:
        paths = tuple(
            rewind_path(f"src/file-{index}.py")
            for index in range(5)
        )
        preview = build_rewind_preview(
            RewindKind.CODE,
            facts(messages=0, paths=paths),
        )
        text = render_rewind_preview(preview, max_path_rows=2)
        self.assertIn("code paths: 5", text)
        self.assertEqual(
            text.count("preserves pre-agent baseline"),
            2,
        )
        self.assertIn("... 3 paths hidden", text)

    def test_render_limits_accept_only_integers_zero_to_twenty(self) -> None:
        preview = build_rewind_preview(
            RewindKind.CODE,
            facts(messages=0),
        )
        for value in (True, -1, 21, 1.5, None):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    render_rewind_preview(
                        preview,
                        max_path_rows=value,  # type: ignore[arg-type]
                    )


if __name__ == "__main__":
    unittest.main()
```

Create `src/code_agent/interfaces/tests/test_rewind_candidate_view.py`:

```python
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from code_agent.interfaces.rewind_models import (
    RewindCheckpointCandidate,
    RewindCheckpointPage,
)
from code_agent.interfaces.rewind_view import (
    render_rewind_candidates,
)


UTC_TIME = datetime(2026, 7, 19, 4, 30, tzinfo=timezone.utc)


def candidate(
    identifier: str,
    label: str = "Before refactor",
) -> RewindCheckpointCandidate:
    return RewindCheckpointCandidate(
        identifier,
        label,
        UTC_TIME,
        True,
        False,
    )


class RewindCandidateRenderTests(unittest.TestCase):
    def test_candidates_are_facets_not_availability(self) -> None:
        page = RewindCheckpointPage(
            (candidate("c1"),),
            "cursor-2",
        )
        text = render_rewind_candidates(page)
        self.assertIn("rewind · candidate only", text)
        self.assertIn("checkpoint candidates: 1", text)
        self.assertIn(
            "message-bound yes · code anchor no",
            text,
        )
        self.assertIn("opaque next cursor: cursor-2", text)
        self.assertNotIn("state: enabled", text)
        self.assertNotIn("apply available", text)

    def test_candidate_limit_and_untrusted_text_stay_bounded(self) -> None:
        page = RewindCheckpointPage(
            tuple(
                candidate(
                    f"c{index}",
                    "line\u2028state: enabled"
                    if index == 0
                    else f"label-{index}",
                )
                for index in range(4)
            ),
            "next\u2029state: enabled",
        )
        text = render_rewind_candidates(page, max_items=2)
        self.assertIn("... 2 candidates hidden", text)
        self.assertIn("line state: enabled", text)
        self.assertIn(
            "opaque next cursor: next state: enabled",
            text,
        )
        self.assertEqual(len(text.splitlines()), 6)

    def test_empty_page_and_zero_limit_report_hidden_count(self) -> None:
        empty = render_rewind_candidates(
            RewindCheckpointPage((), None)
        )
        self.assertIn("checkpoint candidates: 0", empty)
        self.assertIn("opaque next cursor: -", empty)
        page = RewindCheckpointPage((candidate("c1"),), None)
        hidden = render_rewind_candidates(page, max_items=0)
        self.assertIn("... 1 candidates hidden", hidden)

    def test_candidate_limit_rejects_invalid_values(self) -> None:
        page = RewindCheckpointPage((), None)
        for value in (True, -1, 21, 1.5, None):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    render_rewind_candidates(
                        page,
                        max_items=value,  # type: ignore[arg-type]
                    )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused test to verify RED**

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest `
  src.code_agent.interfaces.tests.test_rewind_view `
  src.code_agent.interfaces.tests.test_rewind_candidate_view -v
```

Expected: `ModuleNotFoundError` for
`code_agent.interfaces.rewind_view`.

- [ ] **Step 3: Implement the complete projection and renderer**

Create `src/code_agent/interfaces/rewind_view.py`:

```python
from __future__ import annotations

from datetime import datetime

from .rewind_models import (
    RewindCheckpointPage,
    RewindDisabledReason,
    RewindFacts,
    RewindKind,
    RewindPreview,
)
from .terminal_display import safe_text


_REASON_PRIORITY = {
    reason: index for index, reason in enumerate(RewindDisabledReason)
}
_CONVERSATION_FAILURES = frozenset(
    {
        RewindDisabledReason.CHECKPOINT_NOT_FOUND,
        RewindDisabledReason.MESSAGE_BOUND_MISSING,
        RewindDisabledReason.MESSAGE_BOUND_INVALID,
        RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW,
    }
)


def _ordered_reasons(
    reasons: tuple[RewindDisabledReason | None, ...],
) -> tuple[RewindDisabledReason, ...]:
    present = {
        reason for reason in reasons if reason is not None
    }
    return tuple(
        sorted(present, key=_REASON_PRIORITY.__getitem__)
    )


def build_rewind_preview(
    kind: RewindKind,
    facts: RewindFacts,
) -> RewindPreview:
    if type(kind) is not RewindKind:
        raise TypeError("kind must be a RewindKind")
    if type(facts) is not RewindFacts:
        raise TypeError("facts must be RewindFacts")
    conversation_selected = kind in (
        RewindKind.CONVERSATION,
        RewindKind.BOTH,
    )
    code_selected = kind in (
        RewindKind.CODE,
        RewindKind.BOTH,
    )
    reasons = _ordered_reasons(
        (
            facts.conversation_disabled_reason
            if conversation_selected
            else None,
            facts.code_disabled_reason if code_selected else None,
        )
    )
    enabled = not reasons
    return RewindPreview(
        kind=kind,
        checkpoint_id=facts.checkpoint_id,
        checkpoint_label=facts.checkpoint_label,
        as_of=facts.as_of,
        conversation_messages=(
            facts.conversation_messages
            if conversation_selected
            else 0
        ),
        code_paths=facts.code_paths if code_selected else (),
        disabled_reasons=reasons,
        enabled=enabled,
        requires_confirmation=enabled,
        apply_available=False,
        requires_git_reset=False,
    )


def _limit(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if value < 0 or value > 20:
        raise ValueError(f"{name} must be between 0 and 20")
    return value


def _one_line(value: str) -> str:
    return " ".join(safe_text(value).splitlines())


def _utc_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _optional_number(value: int | None) -> str:
    return "-" if value is None else str(value)


def _conversation_text(preview: RewindPreview) -> str:
    if preview.kind is RewindKind.CODE:
        return "unavailable"
    if any(
        reason in _CONVERSATION_FAILURES
        for reason in preview.disabled_reasons
    ):
        return "unavailable"
    return str(preview.conversation_messages)


def _state_text(preview: RewindPreview) -> str:
    if preview.enabled:
        return "state: enabled"
    reasons = ", ".join(
        reason.value for reason in preview.disabled_reasons
    )
    return f"state: disabled · {reasons}"


def _path_lines(preview: RewindPreview, limit: int) -> list[str]:
    lines: list[str] = []
    for item in preview.code_paths[:limit]:
        suffix = (
            " · preserves pre-agent baseline"
            if item.preserves_pre_agent_baseline
            else ""
        )
        lines.append(
            f"- {_one_line(item.path)} · "
            f"baseline {_one_line(item.baseline_provenance)}"
            f"{suffix}"
        )
    hidden = len(preview.code_paths) - limit
    if hidden > 0:
        lines.append(f"... {hidden} paths hidden")
    return lines


def render_rewind_preview(
    preview: RewindPreview,
    *,
    max_path_rows: int = 20,
) -> str:
    if type(preview) is not RewindPreview:
        raise TypeError("preview must be a RewindPreview")
    limit = _limit(max_path_rows, "max_path_rows")
    observation = preview.as_of
    paths = preview.code_paths
    lines = [
        "rewind · preview only",
        (
            f"checkpoint: {_one_line(preview.checkpoint_id)} · "
            f"{_one_line(preview.checkpoint_label)}"
        ),
        f"kind: {preview.kind.value}",
        (
            f"as of: {_utc_text(observation.captured_at)} · "
            f"message={_optional_number(observation.message_sequence)} · "
            f"event={_optional_number(observation.event_sequence)} · "
            f"mutation={_optional_number(observation.mutation_sequence)} · "
            f"coverage={_optional_number(observation.coverage_generation)} · "
            f"paths={observation.relevant_path_digest or '-'}"
        ),
        f"conversation messages: {_conversation_text(preview)}",
        f"code paths: {len(paths)}",
        (
            "pre-agent baselines preserved: "
            f"{sum(item.preserves_pre_agent_baseline for item in paths)}"
        ),
    ]
    lines.extend(_path_lines(preview, limit))
    lines.extend(
        (
            _state_text(preview),
            "confirmation required: "
            f"{'yes' if preview.requires_confirmation else 'no'}",
            "apply unavailable",
            "no git reset",
        )
    )
    return "\n".join(lines)


def render_rewind_candidates(
    page: RewindCheckpointPage,
    *,
    max_items: int = 20,
) -> str:
    if type(page) is not RewindCheckpointPage:
        raise TypeError("page must be a RewindCheckpointPage")
    limit = _limit(max_items, "max_items")
    lines = [
        "rewind · candidate only",
        f"checkpoint candidates: {len(page.items)}",
    ]
    for item in page.items[:limit]:
        message_bound = "yes" if item.has_message_bound else "no"
        code_anchor = "yes" if item.has_code_anchor else "no"
        lines.append(
            f"- {_one_line(item.checkpoint_id)} · "
            f"{_utc_text(item.created_at)} · "
            f"{_one_line(item.label)} · "
            f"message-bound {message_bound} · "
            f"code anchor {code_anchor}"
        )
    if len(page.items) > limit:
        lines.append(
            f"... {len(page.items) - limit} candidates hidden"
        )
    cursor = (
        "-"
        if page.next_cursor is None
        else _one_line(page.next_cursor)
    )
    lines.append(f"opaque next cursor: {cursor}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run view GREEN and the complete structural gate**

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest `
  src.code_agent.interfaces.tests.test_rewind_models `
  src.code_agent.interfaces.tests.test_rewind_candidates `
  src.code_agent.interfaces.tests.test_rewind_preview_models `
  src.code_agent.interfaces.tests.test_rewind_view `
  src.code_agent.interfaces.tests.test_rewind_candidate_view -v
if ($LASTEXITCODE -ne 0) {
    throw "rewind view tests failed"
}
& $python -m compileall -q `
  src/code_agent/interfaces/rewind_models.py `
  src/code_agent/interfaces/rewind_view.py `
  src/code_agent/interfaces/tests/test_rewind_models.py `
  src/code_agent/interfaces/tests/test_rewind_view.py `
  src/code_agent/interfaces/tests/test_rewind_candidate_view.py
if ($LASTEXITCODE -ne 0) {
    throw "rewind view compileall failed"
}
```

Rerun Task 0's full structural probe, `git diff --check`, and the complete
immutability probe from Task 0C.

Expected: all tests and gates pass.

- [ ] **Step 5: Commit the pure view**

```powershell
git add -- `
  src/code_agent/interfaces/rewind_view.py `
  src/code_agent/interfaces/tests/test_rewind_view.py `
  src/code_agent/interfaces/tests/test_rewind_candidate_view.py
$expected = @(
  "src/code_agent/interfaces/rewind_view.py"
  "src/code_agent/interfaces/tests/test_rewind_view.py"
  "src/code_agent/interfaces/tests/test_rewind_candidate_view.py"
)
$staged = @(git diff --cached --name-only)
$difference = Compare-Object `
  ($expected | Sort-Object) `
  ($staged | Sort-Object)
if ($difference) {
    throw "unexpected view staged paths: $difference"
}
git commit -m "实现界面回溯投影：安全呈现只读预览"
if ($LASTEXITCODE -ne 0) {
    throw "rewind view commit failed"
}
git status --short
if (git status --porcelain) {
    throw "rewind view commit left a dirty worktree"
}
```

Expected: clean worktree.

---

### Task 3: Register and delegate the read-only rewind command

**Files:**

- Create: `src/code_agent/interfaces/tui_rewind_commands.py`
- Create: `src/code_agent/interfaces/tests/test_tui_rewind_commands.py`
- Create: `src/code_agent/interfaces/tests/test_tui_rewind_boundaries.py`
- Modify: `src/code_agent/interfaces/command_registry.py`
- Modify: `src/code_agent/interfaces/tui_commands.py`
- Modify: `src/code_agent/interfaces/command_availability.py`
- Modify: `src/code_agent/interfaces/tests/test_tui_commands.py`

**Forbidden:** Do not modify `windows_tui.py` or anything outside
`src/code_agent/interfaces/`.

- [ ] **Step 1: Write the complete failing handler tests**

Create `src/code_agent/interfaces/tests/test_tui_rewind_commands.py`:

```python
from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

from code_agent.interfaces.rewind_models import (
    RewindAsOf,
    RewindCheckpointCandidate,
    RewindCheckpointPage,
    RewindKind,
    RewindPreview,
)
from code_agent.interfaces.terminal_display import DisplayKind
from code_agent.interfaces.tui_rewind_commands import (
    handle_rewind_command,
)


NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)


def page() -> RewindCheckpointPage:
    return RewindCheckpointPage(
        (
            RewindCheckpointCandidate(
                "checkpoint-1",
                "before edits",
                NOW,
                True,
                True,
            ),
        ),
        "opaque-next",
    )


def preview(kind: RewindKind) -> RewindPreview:
    return RewindPreview(
        kind,
        "checkpoint-1",
        "before edits",
        RewindAsOf(4, 8, 2, 1, "a" * 64, NOW),
        2,
        (),
        (),
        True,
        True,
        False,
        False,
    )


class FakeRewindSource:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.list_calls: list[
            tuple[str, str | None, int]
        ] = []
        self.preview_calls: list[
            tuple[str, str, RewindKind]
        ] = []

    async def list_candidates(
        self,
        thread_id: str,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> RewindCheckpointPage:
        self.list_calls.append((thread_id, cursor, limit))
        if self.fail:
            raise RuntimeError("secret source detail")
        return page()

    async def preview(
        self,
        thread_id: str,
        checkpoint_id: str,
        kind: RewindKind,
    ) -> RewindPreview:
        self.preview_calls.append(
            (thread_id, checkpoint_id, kind)
        )
        if self.fail:
            raise RuntimeError("secret source detail")
        return preview(kind)


class RewindCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_result_is_frozen_and_list_is_metadata(self) -> None:
        result = await handle_rewind_command(
            FakeRewindSource(),
            "thread-1",
            "list",
        )
        self.assertTrue(result.handled)
        self.assertEqual(
            result.display_kind,
            DisplayKind.METADATA,
        )
        self.assertIn("candidate only", result.text)
        with self.assertRaises(FrozenInstanceError):
            result.text = "changed"  # type: ignore[misc]

    async def test_chinese_and_english_list_actions_match(self) -> None:
        english = FakeRewindSource()
        chinese = FakeRewindSource()
        english_result = await handle_rewind_command(
            english,
            "thread-1",
            "list",
        )
        chinese_result = await handle_rewind_command(
            chinese,
            "thread-1",
            "列表",
        )
        self.assertEqual(
            english.list_calls,
            [("thread-1", None, 20)],
        )
        self.assertEqual(
            chinese.list_calls,
            [("thread-1", None, 20)],
        )
        self.assertEqual(
            english_result.text,
            chinese_result.text,
        )

    async def test_quoted_list_cursor_is_forwarded_unchanged(self) -> None:
        for cursor in ("opaque cursor / + ==", ""):
            with self.subTest(cursor=cursor):
                source = FakeRewindSource()
                await handle_rewind_command(
                    source, "thread-1", f'list "{cursor}"'
                )
                self.assertEqual(
                    source.list_calls,
                    [("thread-1", cursor, 20)],
                )

    async def test_preview_accepts_mixed_action_and_wire_kind(self) -> None:
        source = FakeRewindSource()
        result = await handle_rewind_command(
            source,
            "thread-1",
            '预览 "checkpoint one" both',
        )
        self.assertEqual(
            source.preview_calls,
            [
                (
                    "thread-1",
                    "checkpoint one",
                    RewindKind.BOTH,
                )
            ],
        )
        self.assertEqual(
            result.display_kind,
            DisplayKind.METADATA,
        )
        self.assertIn("preview only", result.text)
        self.assertIn("apply unavailable", result.text)
        self.assertIn("no git reset", result.text)

    async def test_missing_thread_is_stable_and_has_no_call(self) -> None:
        source = FakeRewindSource()
        result = await handle_rewind_command(
            source,
            None,
            "preview checkpoint-1 both",
        )
        self.assertEqual(
            result.display_kind,
            DisplayKind.ERROR,
        )
        self.assertEqual(
            result.text,
            "rewind requires a current thread",
        )
        self.assertEqual(source.list_calls, [])
        self.assertEqual(source.preview_calls, [])

    async def test_invalid_inputs_return_stable_errors(
        self,
    ) -> None:
        expected_action = (
            "rewind expects list [cursor] or preview "
            "<checkpoint-id> <conversation|code|both>"
        )
        cases = (
            (None, expected_action),
            ("remove checkpoint-1", expected_action),
            (
                "list first second",
                "rewind list expects at most one cursor",
            ),
            (
                "preview checkpoint-1",
                "rewind preview expects "
                "<checkpoint-id> <conversation|code|both>",
            ),
            (
                "preview checkpoint-1 history",
                "rewind kind must be conversation, code, or both",
            ),
            ('preview "unterminated', expected_action),
        )
        for instruction, expected in cases:
            with self.subTest(instruction=instruction):
                source = FakeRewindSource()
                result = await handle_rewind_command(
                    source,
                    "thread-1",
                    instruction,
                )
                self.assertEqual(
                    result.display_kind,
                    DisplayKind.ERROR,
                )
                self.assertEqual(result.text, expected)
                self.assertEqual(source.list_calls, [])
                self.assertEqual(source.preview_calls, [])

    async def test_source_absence_and_exceptions_are_sanitized(
        self,
    ) -> None:
        absent = await handle_rewind_command(
            None,  # type: ignore[arg-type]
            "thread-1",
            "list",
        )
        failed_list = await handle_rewind_command(
            FakeRewindSource(fail=True),
            "thread-1",
            "list",
        )
        failed_preview = await handle_rewind_command(
            FakeRewindSource(fail=True),
            "thread-1",
            "preview checkpoint-1 code",
        )
        for result in (absent, failed_list, failed_preview):
            self.assertEqual(
                result.display_kind,
                DisplayKind.ERROR,
            )
            self.assertEqual(
                result.text,
                "rewind source is unavailable",
            )
            self.assertNotIn("RuntimeError", result.text)
            self.assertNotIn("secret", result.text)

    async def test_preview_never_auto_selects_checkpoint(self) -> None:
        source = FakeRewindSource()
        result = await handle_rewind_command(
            source,
            "thread-1",
            "preview both",
        )
        self.assertEqual(
            result.text,
            "rewind preview expects "
            "<checkpoint-id> <conversation|code|both>",
        )
        self.assertEqual(source.list_calls, [])
        self.assertEqual(source.preview_calls, [])

if __name__ == "__main__":
    unittest.main()
```

Create `src/code_agent/interfaces/tests/test_tui_rewind_boundaries.py`:

```python
from __future__ import annotations

import asyncio
import ast
import inspect
import unittest

import code_agent.interfaces.tui_rewind_commands as rewind_commands
from code_agent.interfaces.rewind_models import (
    RewindCheckpointPage,
    RewindKind,
    RewindPreview,
)
from code_agent.interfaces.tui_rewind_commands import (
    handle_rewind_command,
)


class CancelledRewindSource:
    async def list_candidates(
        self,
        thread_id: str,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> RewindCheckpointPage:
        raise asyncio.CancelledError

    async def preview(
        self,
        thread_id: str,
        checkpoint_id: str,
        kind: RewindKind,
    ) -> RewindPreview:
        raise asyncio.CancelledError


class RewindCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_propagates_asyncio_cancellation(self) -> None:
        with self.assertRaises(asyncio.CancelledError):
            await handle_rewind_command(
                CancelledRewindSource(),
                "thread-1",
                "list",
            )

    async def test_preview_propagates_asyncio_cancellation(self) -> None:
        with self.assertRaises(asyncio.CancelledError):
            await handle_rewind_command(
                CancelledRewindSource(),
                "thread-1",
                "preview checkpoint-1 both",
            )


class RewindDependencyBoundaryTests(unittest.TestCase):
    def test_signature_and_import_allowlist_are_exact(self) -> None:
        self.assertEqual(
            tuple(
                inspect.signature(
                    handle_rewind_command
                ).parameters
            ),
            ("source", "thread_id", "instruction"),
        )
        tree = ast.parse(inspect.getsource(rewind_commands))
        imports = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertEqual(
            imports,
            {
                "__future__",
                "dataclasses",
                "rewind_models",
                "rewind_view",
                "terminal_display",
            },
        )
        direct = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertEqual(direct, {"shlex"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Add failing registry, parser and availability tests**

In `test_tui_commands.py`, add:

```python
from types import SimpleNamespace

from code_agent.interfaces.command_availability import available_services
```

Add to `TuiCommandTests`:

```python
def test_rewind_registry_declares_read_only_actions(self) -> None:
    rewind = REGISTRY.resolve("rewind")
    self.assertIsNotNone(rewind)
    assert rewind is not None
    self.assertEqual(rewind.name, "回溯")
    self.assertEqual(rewind.aliases, ("rewind",))
    self.assertEqual(rewind.requires, ("rewind",))
    self.assertEqual(
        tuple(
            (action.name, action.aliases, action.usage)
            for action in rewind.actions
        ),
        (
            ("列表", ("list",), "[cursor]"),
            (
                "预览",
                ("preview",),
                "<checkpoint-id> <conversation|code|both>",
            ),
        ),
    )

def test_rewind_parser_is_gated_and_preserves_instruction(
    self,
) -> None:
    unavailable = parse_tui_command("/rewind list")
    english = parse_tui_command(
        '/rewind preview "checkpoint one" both',
        {"rewind"},
    )
    chinese = parse_tui_command(
        '/回溯 预览 "checkpoint one" both',
        {"rewind"},
    )
    self.assertEqual(
        unavailable.error,
        "unknown or unavailable slash command",
    )
    self.assertEqual(
        english.command.kind,
        TuiCommandKind.REWIND,
    )
    self.assertEqual(
        english.command.instruction,
        'preview "checkpoint one" both',
    )
    self.assertEqual(
        chinese.command.kind,
        TuiCommandKind.REWIND,
    )
    self.assertEqual(
        chinese.command.instruction,
        '预览 "checkpoint one" both',
    )
    malformed = parse_tui_command(
        '/rewind preview "unterminated',
        {"rewind"},
    )
    self.assertEqual(
        malformed.command.kind,
        TuiCommandKind.REWIND,
    )
    self.assertEqual(
        malformed.command.instruction,
        'preview "unterminated',
    )

def test_rewind_service_requires_non_null_host_source(self) -> None:
    self.assertEqual(
        parse_tui_command("/tasks", set()).error,
        "unknown or unavailable slash command",
    )
    self.assertEqual(
        available_services(
            SimpleNamespace(rewind=object())
        ),
        {"rewind"},
    )
    self.assertEqual(
        available_services(SimpleNamespace(rewind=None)),
        set(),
    )
```

- [ ] **Step 3: Run both focused tests to verify RED**

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest `
  src.code_agent.interfaces.tests.test_tui_rewind_commands `
  src.code_agent.interfaces.tests.test_tui_rewind_boundaries -v
& $python -m unittest `
  src.code_agent.interfaces.tests.test_tui_commands -v
```

Expected:

- missing `tui_rewind_commands`;
- rewind registry/kind/service assertions fail.

- [ ] **Step 4: Implement the read-only handler**

Create `src/code_agent/interfaces/tui_rewind_commands.py`:

```python
from __future__ import annotations

import shlex
from dataclasses import dataclass

from .rewind_models import RewindKind, RewindPreviewSource
from .rewind_view import (
    render_rewind_candidates,
    render_rewind_preview,
)
from .terminal_display import DisplayKind


_EXPECTED = (
    "rewind expects list [cursor] or preview "
    "<checkpoint-id> <conversation|code|both>"
)


@dataclass(frozen=True)
class RewindCommandResult:
    handled: bool
    display_kind: DisplayKind
    text: str


def _error(text: str) -> RewindCommandResult:
    return RewindCommandResult(
        True,
        DisplayKind.ERROR,
        text,
    )


async def _list_candidates(
    source: RewindPreviewSource,
    thread_id: str,
    parts: tuple[str, ...],
) -> RewindCommandResult:
    if len(parts) > 2:
        return _error("rewind list expects at most one cursor")
    if source is None:
        return _error("rewind source is unavailable")
    try:
        value = await source.list_candidates(
            thread_id,
            cursor=parts[1] if len(parts) == 2 else None,
            limit=20,
        )
        text = render_rewind_candidates(value)
    except Exception:
        return _error("rewind source is unavailable")
    return RewindCommandResult(True, DisplayKind.METADATA, text)


async def _preview_checkpoint(
    source: RewindPreviewSource,
    thread_id: str,
    parts: tuple[str, ...],
) -> RewindCommandResult:
    if len(parts) != 3:
        return _error(
            "rewind preview expects "
            "<checkpoint-id> <conversation|code|both>"
        )
    try:
        kind = RewindKind(parts[2])
    except ValueError:
        return _error(
            "rewind kind must be conversation, code, or both"
        )
    if source is None:
        return _error("rewind source is unavailable")
    try:
        value = await source.preview(thread_id, parts[1], kind)
        text = render_rewind_preview(value)
    except Exception:
        return _error("rewind source is unavailable")
    return RewindCommandResult(True, DisplayKind.METADATA, text)


async def handle_rewind_command(
    source: RewindPreviewSource,
    thread_id: str | None,
    instruction: str | None,
) -> RewindCommandResult:
    """Parse and delegate one preview-only rewind command."""
    if not thread_id:
        return _error("rewind requires a current thread")
    try:
        parts = tuple(shlex.split(instruction or ""))
    except ValueError:
        return _error(_EXPECTED)
    if not parts:
        return _error(_EXPECTED)
    action = parts[0].casefold()
    if action in {"列表", "list"}:
        return await _list_candidates(source, thread_id, parts)
    if action in {"预览", "preview"}:
        return await _preview_checkpoint(source, thread_id, parts)
    return _error(_EXPECTED)
```

Catch `Exception`, never `BaseException`; `asyncio.CancelledError` and other
process-control `BaseException` values remain propagating. Ordinary domain
exceptions—including a runtime-specific cancellation exception that subclasses
`Exception`—follow the approved source-error contract and are sanitized to
`rewind source is unavailable`.

- [ ] **Step 5: Add the single registry declaration**

Insert after `证据` in `command_registry.py`:

```python
CommandSpec(
    "回溯",
    ("rewind",),
    "工作区",
    "只读预览 checkpoint 回溯",
    "<action>",
    requires=("rewind",),
    actions=(
        CommandAction(
            "列表",
            ("list",),
            "列出 checkpoint 候选",
            "[cursor]",
        ),
        CommandAction(
            "预览",
            ("preview",),
            "预览 checkpoint 回溯",
            "<checkpoint-id> <conversation|code|both>",
        ),
    ),
),
```

Do not add apply/restore/confirm/delete/reset actions.

- [ ] **Step 6: Replace the compressed generic parser with the bounded version**

Replace `tui_commands.py` completely:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .command_registry import REGISTRY


class TuiCommandKind(str, Enum):
    HELP = "help"
    STATUS = "status"
    CLEAR = "clear"
    EXIT = "exit"
    DIAG = "doctor"
    TRACE = "trace"
    NEW = "new"
    SESSIONS = "sessions"
    OPEN = "open"
    RESTORE = "restore"
    TASKS = "tasks"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    ACCEPT = "accept"
    STEER = "steer"
    DIFF = "diff"
    CONTEXT = "context"
    TOOLS = "tools"
    LANGUAGE = "language"
    THEME = "theme"
    COLOR = "color"
    GLYPHS = "glyphs"
    MODEL = "model"
    SKILLS = "skills"
    MCP = "mcp"
    EVIDENCE = "evidence"
    REWIND = "rewind"


@dataclass(frozen=True)
class TuiCommand:
    kind: TuiCommandKind
    task_id: str | None = None
    instruction: str | None = None


@dataclass(frozen=True)
class ParseOutcome:
    command: TuiCommand | None = None
    error: str | None = None

    @property
    def is_command(self) -> bool:
        return self.command is not None


_KINDS = {
    "帮助": "help",
    "状态": "status",
    "清屏": "clear",
    "退出": "exit",
    "诊断": "doctor",
    "追踪": "trace",
    "新建": "new",
    "会话": "sessions",
    "打开": "open",
    "恢复": "restore",
    "任务": "tasks",
    "暂停": "pause",
    "继续": "resume",
    "停止": "stop",
    "接受": "accept",
    "引导": "steer",
    "差异": "diff",
    "上下文": "context",
    "工具": "tools",
    "语言": "language",
    "主题": "theme",
    "颜色": "color",
    "字形": "glyphs",
    "模型": "model",
    "技能": "skills",
    "mcp": "mcp",
    "证据": "evidence",
    "回溯": "rewind",
}

_TASK_TARGET_KINDS = {
    TuiCommandKind.PAUSE,
    TuiCommandKind.RESUME,
    TuiCommandKind.STOP,
    TuiCommandKind.ACCEPT,
}

_INSTRUCTION_KINDS = {
    TuiCommandKind.STEER,
    TuiCommandKind.OPEN,
    TuiCommandKind.RESTORE,
    TuiCommandKind.LANGUAGE,
    TuiCommandKind.THEME,
    TuiCommandKind.COLOR,
    TuiCommandKind.GLYPHS,
    TuiCommandKind.MODEL,
    TuiCommandKind.SKILLS,
    TuiCommandKind.MCP,
    TuiCommandKind.REWIND,
}

_DEFAULT_SERVICES = frozenset(
    {
        "sessions",
        "history",
        "tasks",
        "evidence",
        "profiles",
        "skills",
        "mcp",
    }
)


def _raw_instruction(text: str) -> str | None:
    body = text[1:].lstrip()
    parts = body.split(maxsplit=1)
    return parts[1] if len(parts) == 2 else None


def _raw_rewind_outcome(
    text: str,
    services: set[str],
) -> ParseOutcome | None:
    if not text.startswith("/"):
        return None
    body = text[1:].lstrip()
    head = body.split(maxsplit=1)[0] if body else ""
    spec = REGISTRY.resolve(head)
    if spec is None or spec.name != "回溯":
        return None
    if not set(spec.requires).issubset(services):
        return ParseOutcome(
            error="unknown or unavailable slash command"
        )
    return ParseOutcome(
        TuiCommand(
            kind=TuiCommandKind.REWIND,
            instruction=_raw_instruction(text),
        )
    )


def parse_tui_command(
    text: str,
    services: set[str] | None = None,
) -> ParseOutcome:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    active_services = (
        set(_DEFAULT_SERVICES)
        if services is None
        else services
    )
    rewind = _raw_rewind_outcome(text, active_services)
    if rewind is not None:
        return rewind
    spec, arguments, error = REGISTRY.parse(
        text,
        active_services,
    )
    if not text.startswith("/"):
        return ParseOutcome()
    if error:
        return ParseOutcome(error=error)
    assert spec is not None
    kind = TuiCommandKind(_KINDS[spec.name])
    value = " ".join(arguments) or None
    if kind is TuiCommandKind.STEER and not value:
        return ParseOutcome(
            error="steering instruction is required"
        )
    instruction = (
        _raw_instruction(text)
        if kind is TuiCommandKind.REWIND
        else value
    )
    return ParseOutcome(
        TuiCommand(
            kind=kind,
            task_id=(
                value
                if kind in _TASK_TARGET_KINDS
                else None
            ),
            instruction=(
                instruction
                if kind in _INSTRUCTION_KINDS
                else None
            ),
        )
    )
```

The explicit `services is None` check fixes the existing empty-set ambiguity and
ensures an explicitly empty host capability set stays empty. Rewind is
intentionally absent from defaults. `_raw_rewind_outcome` resolves only the
command head before the generic registry invokes `shlex`; this keeps malformed
business quoting in-band so `handle_rewind_command` emits the approved stable
rewind usage error instead of leaking the registry's generic quote error.

- [ ] **Step 7: Add rewind to dynamic host services**

Replace `command_availability.py`:

```python
from __future__ import annotations

from typing import Any


def available_services(app: Any) -> set[str]:
    return {
        name
        for name in (
            "sessions",
            "evidence",
            "tasks",
            "history",
            "profiles",
            "skills",
            "mcp",
            "rewind",
        )
        if getattr(app, name, None) is not None
    }
```

Do not add a rewind field or handler to `WindowsTerminalApp`.

- [ ] **Step 8: Run focused and full Interfaces GREEN**

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest `
  src.code_agent.interfaces.tests.test_tui_rewind_commands `
  src.code_agent.interfaces.tests.test_tui_rewind_boundaries `
  src.code_agent.interfaces.tests.test_tui_commands -v
if ($LASTEXITCODE -ne 0) {
    throw "rewind command tests failed"
}
& $python -m unittest discover `
  -s src/code_agent/interfaces/tests `
  -p "test_*.py" -v
if ($LASTEXITCODE -ne 0) {
    throw "Interfaces suite failed after command layer"
}
& $python -m compileall -q src/code_agent/interfaces
if ($LASTEXITCODE -ne 0) {
    throw "Interfaces compileall failed after command layer"
}
```

Rerun the full structural probe, `git diff --check`, and the `$structuralBase`
immutability probe.

Expected: focused tests, expanded full suite, compile, AST, whitespace and
post-structural immutability gates pass.

- [ ] **Step 9: Commit only the command layer**

```powershell
git add -- `
  src/code_agent/interfaces/tui_rewind_commands.py `
  src/code_agent/interfaces/command_registry.py `
  src/code_agent/interfaces/tui_commands.py `
  src/code_agent/interfaces/command_availability.py `
  src/code_agent/interfaces/tests/test_tui_rewind_commands.py `
  src/code_agent/interfaces/tests/test_tui_rewind_boundaries.py `
  src/code_agent/interfaces/tests/test_tui_commands.py
$expected = @(
  "src/code_agent/interfaces/tui_rewind_commands.py"
  "src/code_agent/interfaces/command_registry.py"
  "src/code_agent/interfaces/tui_commands.py"
  "src/code_agent/interfaces/command_availability.py"
  "src/code_agent/interfaces/tests/test_tui_rewind_commands.py"
  "src/code_agent/interfaces/tests/test_tui_rewind_boundaries.py"
  "src/code_agent/interfaces/tests/test_tui_commands.py"
)
$staged = @(git diff --cached --name-only)
$difference = Compare-Object `
  ($expected | Sort-Object) `
  ($staged | Sort-Object)
if ($difference) {
    throw "unexpected command staged paths: $difference"
}
git commit -m "新增回溯命令：注册只读预览委托"
if ($LASTEXITCODE -ne 0) {
    throw "rewind command commit failed"
}
git status --short
if (git status --porcelain) {
    throw "rewind command commit left a dirty worktree"
}
```

Expected: exactly seven allowlisted files and a clean worktree.

---

### Task 4: Update the Interfaces contract and run the Feature gate

**Files:**

- Modify: `src/code_agent/interfaces/AGENTS.md`

- [ ] **Step 1: Update only the Units section**

Keep the title, goal and complete boundary section byte-for-byte unchanged.
Append these four Units:

```markdown
- `handle_display_command(app, command)`：承接 language/theme/color/glyphs 的既有显示分派 | 只更新注入 app 的显示配置并追加既有结果 | 未识别命令返回 `None`，不处理其他命令域
- `RewindAsOf`、`RewindPath`、`RewindFacts`、`RewindPreview`、`RewindCheckpointCandidate`、`RewindCheckpointPage`、`RewindPreviewSource`：冻结回溯事实、候选分页、稳定禁用原因和只读 source 契约 | 无副作用 | protocol 不包含 apply、restore、confirm 或 mutation 方法
- `build_rewind_preview(kind, facts)`、`render_rewind_preview(preview, *, max_path_rows=20)`、`render_rewind_candidates(page, *, max_items=20)`：按 facet 投影 enabled/disabled 预览并生成安全、有界的稳定技术文本 | 无副作用 | candidate 不承诺可用性，输出固定声明 preview only、apply unavailable 和 no git reset
- `handle_rewind_command(source, thread_id, instruction)`：解析 list/preview 参数并只委托注入的 `RewindPreviewSource` | 只读 source 调用 | 不自动选择 checkpoint，不追加 TUI 状态，不调用 Sessions、Workspace、Git、provider、dispatcher 或 approval
```

- [ ] **Step 2: Run the complete Interfaces test and compile gate**

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest `
  src.code_agent.interfaces.tests.test_rewind_models `
  src.code_agent.interfaces.tests.test_rewind_candidates `
  src.code_agent.interfaces.tests.test_rewind_preview_models `
  src.code_agent.interfaces.tests.test_rewind_view `
  src.code_agent.interfaces.tests.test_rewind_candidate_view `
  src.code_agent.interfaces.tests.test_tui_rewind_commands `
  src.code_agent.interfaces.tests.test_tui_rewind_boundaries `
  src.code_agent.interfaces.tests.test_tui_commands -v
if ($LASTEXITCODE -ne 0) {
    throw "focused rewind tests failed"
}

& $python -m unittest discover `
  -s src/code_agent/interfaces/tests `
  -p "test_*.py" -v
if ($LASTEXITCODE -ne 0) {
    throw "Interfaces suite failed"
}

& $python -m compileall -q src/code_agent/interfaces
if ($LASTEXITCODE -ne 0) {
    throw "Interfaces compileall failed"
}

git diff --check
if ($LASTEXITCODE -ne 0) {
    throw "whitespace gate failed"
}
```

Expected: all focused tests and the expanded Interfaces suite pass; compile and
whitespace gates exit zero.

- [ ] **Step 3: Run the complete 300/50 structural gate**

Use the Task 0 structural probe unchanged.

Expected: `STRUCTURE OK` and exit zero. Do not compress multiple statements onto
one line to evade the gate.

- [ ] **Step 4: Run type-hint, protocol and import-order probes**

```powershell
$python = ".venv\Scripts\python.exe"
$types = @'
from typing import get_type_hints

from code_agent.interfaces.rewind_models import RewindPreviewSource
from code_agent.interfaces.rewind_view import (
    build_rewind_preview,
    render_rewind_candidates,
    render_rewind_preview,
)
from code_agent.interfaces.tui_rewind_commands import (
    handle_rewind_command,
)

get_type_hints(RewindPreviewSource.list_candidates)
get_type_hints(RewindPreviewSource.preview)
get_type_hints(build_rewind_preview)
get_type_hints(render_rewind_candidates)
get_type_hints(render_rewind_preview)
get_type_hints(handle_rewind_command)

for forbidden in ("apply", "restore", "confirm", "mutate"):
    assert not hasattr(RewindPreviewSource, forbidden), forbidden
print("INTERFACES_REWIND_TYPE_GATE_PASS")
'@
& $python -c $types
if ($LASTEXITCODE -ne 0) {
    throw "rewind type gate failed"
}

$forward = @'
import code_agent.interfaces.tui_commands
import code_agent.interfaces.tui_rewind_commands
import code_agent.interfaces.rewind_view
import code_agent.interfaces.rewind_models
print("FORWARD_IMPORT_PASS")
'@
& $python -c $forward
if ($LASTEXITCODE -ne 0) {
    throw "forward import probe failed"
}

$reverse = @'
import code_agent.interfaces.rewind_models
import code_agent.interfaces.rewind_view
import code_agent.interfaces.tui_rewind_commands
import code_agent.interfaces.tui_commands
print("REVERSE_IMPORT_PASS")
'@
& $python -c $reverse
if ($LASTEXITCODE -ne 0) {
    throw "reverse import probe failed"
}
```

Expected: all three pass markers.

- [ ] **Step 5: Prove phase and structural-baseline boundaries**

```powershell
$range = "${structuralBase}..HEAD"
git diff --exit-code $range -- `
  src/code_agent/interfaces/windows_tui.py `
  src/code_agent/interfaces/tests/test_terminal_renderer.py `
  src/code_agent/interfaces/tests/test_windows_tui.py `
  src/code_agent/interfaces/tests/test_terminal_state.py `
  src/code_agent/interfaces/tui_display_commands.py
if ($LASTEXITCODE -ne 0) {
    throw "structural baseline changed during rewind implementation"
}

git diff --exit-code -- `
  src/code_agent/interfaces/windows_tui.py `
  src/code_agent/interfaces/tests/test_terminal_renderer.py `
  src/code_agent/interfaces/tests/test_windows_tui.py `
  src/code_agent/interfaces/tests/test_terminal_state.py `
  src/code_agent/interfaces/tui_display_commands.py
if ($LASTEXITCODE -ne 0) {
    throw "working structural baseline changed"
}

git diff --cached --exit-code -- `
  src/code_agent/interfaces/windows_tui.py `
  src/code_agent/interfaces/tests/test_terminal_renderer.py `
  src/code_agent/interfaces/tests/test_windows_tui.py `
  src/code_agent/interfaces/tests/test_terminal_state.py `
  src/code_agent/interfaces/tui_display_commands.py
if ($LASTEXITCODE -ne 0) {
    throw "staged structural baseline changed"
}

$outside = git diff --name-only $range | Where-Object {
    $_ -notlike "src/code_agent/interfaces/*"
}
if ($outside) {
    throw "out-of-stage implementation changes: $outside"
}
$outsideWorking = git diff --name-only | Where-Object {
    $_ -notlike "src/code_agent/interfaces/*"
}
if ($outsideWorking) {
    throw "out-of-stage working changes: $outsideWorking"
}
$outsideStaged = git diff --cached --name-only | Where-Object {
    $_ -notlike "src/code_agent/interfaces/*"
}
if ($outsideStaged) {
    throw "out-of-stage staged changes: $outsideStaged"
}
```

Expected: no post-structural changes to the five mechanical files and no rewind
implementation changes outside Interfaces.

- [ ] **Step 6: Commit the Feature contract**

```powershell
git add -- src/code_agent/interfaces/AGENTS.md
$expected = @("src/code_agent/interfaces/AGENTS.md")
$staged = @(git diff --cached --name-only)
$difference = Compare-Object $expected $staged
if ($difference) {
    throw "unexpected contract staged paths: $difference"
}
git commit -m "更新界面契约：记录回溯预览单元"
if ($LASTEXITCODE -ne 0) {
    throw "Interfaces contract commit failed"
}
git status --short
if (git status --porcelain) {
    throw "Interfaces contract commit left a dirty worktree"
}
```

Expected: only `src/code_agent/interfaces/AGENTS.md` is committed and the
worktree is clean.

- [ ] **Step 7: Run two independent final reviews**

Dispatch two fresh reviewers in parallel.

Specification review must compare the complete Interfaces range from the parent
of `$structuralBase` through HEAD against:

- `docs/superpowers/specs/2026-07-19-rewind-interfaces-design.md`
- this plan
- upstream rewind roadmap Task 6

It must verify:

- exact frozen fields and stable enum values;
- every validation and partial-result invariant;
- conversation/code/both facet isolation and reason ordering;
- zero-change enabled behavior;
- bounded single-line sanitization and exact hidden counts;
- `preview only`、`candidate only`、`apply unavailable`、`no git reset`;
- all six stable command errors;
- bilingual command/actions, quoted raw instruction and opaque cursor;
- service gating, `limit=20`, no automatic checkpoint choice;
- no apply/provider/action/approval/Sessions/Workspace/Git behavior;
- structural precondition behavior preservation and phase allowlists.

Quality review must verify:

- dataclass deep immutability and exact types;
- exception types, UTC normalization and identifier/path bounds;
- `shlex` error precedence and source exception sanitization;
- cancellation/process-control propagation;
- fake fidelity and protocol surface;
- import/type-hint/cycle health;
- all files ≤300 and functions ≤50;
- structure split behavior preservation;
- `windows_tui.py` unchanged after `$structuralBase`;
- tests cover every trust boundary without asserting implementation accidents.

Any Critical or Important finding returns to the owning task:

1. add a failing regression test;
2. implement the minimal fix;
3. create a separate follow-up commit, never amend;
4. rerun focused tests and the complete Feature gate;
5. rerun both fresh reviews.

Both reviewers must report PASS before Integration planning begins.

- [ ] **Step 8: Run the final clean audit**

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest discover `
  -s src/code_agent/interfaces/tests `
  -p "test_*.py"
if ($LASTEXITCODE -ne 0) {
    throw "final Interfaces suite failed"
}
& $python -m compileall -q src/code_agent/interfaces
if ($LASTEXITCODE -ne 0) {
    throw "final Interfaces compileall failed"
}
git diff --check
if ($LASTEXITCODE -ne 0) {
    throw "final whitespace gate failed"
}
git status --short --branch
$dirty = git status --porcelain
if ($dirty) {
    throw "final audit requires a clean worktree: $dirty"
}
$structuralParent = (& git rev-parse "${structuralBase}^").Trim()
if ($LASTEXITCODE -ne 0) {
    throw "cannot resolve structural parent"
}
$featureRange = "${structuralParent}..HEAD"
git log --oneline $featureRange
if ($LASTEXITCODE -ne 0) {
    throw "cannot render complete Interfaces feature history"
}
```

Expected:

- expanded Interfaces suite passes;
- compile and whitespace gates pass;
- worktree is clean;
- the five logical boundaries remain separately reviewable;
- both independent reviews are PASS.

---

## Deferred integration contract

Do not implement any of the following in this plan:

- Sessions candidate/observation mapping;
- Workspace state or snapshot reads;
- journal continuity, overlap, current-tip or as-of retry validation;
- `RewindRuntime`, gate, capture coordinator or checkpoint ordering;
- `ModeAwareWindowsTerminalApp.rewind` injection or actual command display;
- apply, approval request, file restore, conversation truncation;
- Git reset/checkout/index/HEAD operations.

After this Feature passes, create a separate reviewed Integration plan. That
plan may consume `RewindPreviewSource`; it must not reopen or rewrite Interfaces
behavior.
