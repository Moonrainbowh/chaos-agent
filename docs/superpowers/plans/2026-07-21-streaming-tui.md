# Windows TUI Streaming Answer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render provider text deltas in a bounded temporary assistant area and append the completed assistant Markdown to terminal scrollback exactly once.

**Architecture:** `TerminalState` owns provisional answer text, `terminal_tail` renders that text above the composer, and `WindowsTerminalApp` coalesces model-event repaints through the existing animation loop. Persisted `MESSAGE_ADDED` remains authoritative; partial text is display-only and never verification evidence.

**Tech Stack:** Python 3.10+, asyncio, unittest, existing ANSI-safe Windows Terminal renderer.

## Global Constraints

- Keep the normal terminal scrollback append-only; do not use alternate-screen or full-screen clear sequences.
- Strip untrusted terminal control sequences through existing local display helpers.
- Never render `REASONING_DELTA`.
- Final assistant Markdown must be appended exactly once.
- A partial answer must use `DisplayKind.PARTIAL_AGENT` and must not enter persisted conversation history or completion evidence.
- Feature files remain at most 300 lines and functions at most 50 lines.
- Follow the repository's requirement → implementation → integration phase boundaries.

---

## File Structure

- Modify `src/code_agent/interfaces/AGENTS.md`: record the streaming-answer boundary and Units.
- Modify `src/code_agent/interfaces/terminal_display.py`: add the trusted partial-agent display kind.
- Modify `src/code_agent/interfaces/terminal_state.py`: expose draft text and freeze interrupted drafts.
- Modify `src/code_agent/interfaces/terminal_tail.py`: lay out a bounded temporary assistant block.
- Modify `src/code_agent/interfaces/terminal_renderer.py`: style final and partial assistant entries distinctly.
- Modify `src/code_agent/interfaces/windows_tui.py`: pass draft state to the tail and make non-model boundaries flush immediately.
- Modify `src/code_agent/interfaces/tui_lifecycle.py`: coalesce model delta repainting at a fixed short interval.
- Modify `src/code_agent/interfaces/tests/test_terminal_state.py`: state transition coverage.
- Modify `src/code_agent/interfaces/tests/test_terminal_tail.py`: geometry, sanitization, and clipping coverage.
- Modify `src/code_agent/interfaces/tests/test_windows_tui.py`: end-to-end provisional/final output coverage.

### Task 1: Lock the Interfaces requirement contract

**Files:**
- Modify: `src/code_agent/interfaces/AGENTS.md`

**Interfaces:**
- Consumes: approved design `docs/superpowers/specs/2026-07-21-streaming-tui-design.md`.
- Produces: requirement boundary used by Tasks 2–5; no new Unit signature yet.

- [ ] **Step 1: Add the requirement boundary**

Add these bullets under `## 边界`:

```markdown
- 负责：把当前模型回合的文本增量呈现在输入区上方的有界临时 assistant 区；完整消息到达后清除临时区，并只向普通终端滚动区固化一次最终 Markdown。
- 负责：取消或错误后的临时正文必须明确标记为未完成回答；它不是会话消息、验证证据或完成判定。
- 不负责：渲染原始 reasoning、把不完整 Markdown 直接写入滚动区，或通过高频 delta 重写历史输出。
```

- [ ] **Step 2: Verify the contract-only diff**

Run: `git diff --check -- src/code_agent/interfaces/AGENTS.md`

Expected: exit code 0 and no output.

- [ ] **Step 3: Commit the requirement stage**

```powershell
git add -- src/code_agent/interfaces/AGENTS.md
git commit -m "明确 TUI 流式正文边界" -m "- 变更内容：约束临时正文、最终固化和未完成回答语义。" -m "- 变更原因：在实现前锁定追加式终端边界。" -m "- 验证情况：git diff --check 通过。"
```

### Task 2: Project provisional and partial answers in TerminalState

**Files:**
- Modify: `src/code_agent/interfaces/terminal_display.py`
- Modify: `src/code_agent/interfaces/terminal_state.py`
- Modify: `src/code_agent/interfaces/tests/test_terminal_state.py`
- Modify: `src/code_agent/interfaces/AGENTS.md`

**Interfaces:**
- Consumes: `ModelEventKind.TEXT_DELTA`, `EventKind.MESSAGE_ADDED`, `EventKind.CANCELLED`, `EventKind.ERROR`.
- Produces: `DisplayKind.PARTIAL_AGENT`, `TerminalState.draft_answer: str`, `TerminalState.has_draft: bool`.

- [ ] **Step 1: Write failing state tests**

Add tests that use the existing `AgentEvent` helpers:

```python
def test_text_delta_is_visible_as_draft_before_final_message(self) -> None:
    state = TerminalState()
    state.apply(AgentEvent(EventKind.MODEL_EVENT, {
        "event": ModelEvent(ModelEventKind.TEXT_DELTA, text="hello ").to_dict()
    }))
    state.apply(AgentEvent(EventKind.MODEL_EVENT, {
        "event": ModelEvent(ModelEventKind.TEXT_DELTA, text="world").to_dict()
    }))
    self.assertEqual(state.draft_answer, "hello world")
    self.assertTrue(state.has_draft)
    self.assertEqual(state.entries, [])

def test_completed_message_clears_draft_and_adds_one_final_entry(self) -> None:
    state = TerminalState()
    state.apply(AgentEvent(EventKind.MODEL_EVENT, {
        "event": ModelEvent(ModelEventKind.TEXT_DELTA, text="answer").to_dict()
    }))
    message = Message(role="assistant", content="answer")
    state.apply(AgentEvent(EventKind.MESSAGE_ADDED, {"message": message.to_dict()}))
    state.apply(AgentEvent(EventKind.COMPLETED, {}))
    self.assertEqual(state.draft_answer, "")
    self.assertEqual([entry.text for entry in state.entries], ["answer"])

def test_cancelled_draft_becomes_non_conversation_partial_entry(self) -> None:
    state = TerminalState()
    state.apply(AgentEvent(EventKind.MODEL_EVENT, {
        "event": ModelEvent(ModelEventKind.TEXT_DELTA, text="unfinished").to_dict()
    }))
    state.apply(AgentEvent(EventKind.CANCELLED, {"reason": "user requested pause"}))
    self.assertEqual(state.entries[-1].kind, DisplayKind.PARTIAL_AGENT)
    self.assertEqual(state.entries[-1].text, "unfinished")
    self.assertEqual(state.transcript, [])
    self.assertFalse(state.has_draft)
```

- [ ] **Step 2: Run the tests and confirm failure**

Run: `uv run --with regex python -m unittest src.code_agent.interfaces.tests.test_terminal_state -v`

Expected: FAIL because `PARTIAL_AGENT`, `draft_answer`, and `has_draft` do not exist.

- [ ] **Step 3: Implement the state projection**

Add the enum value and properties:

```python
class DisplayKind(str, Enum):
    USER = "user"
    AGENT = "agent"
    PARTIAL_AGENT = "partial_agent"
    # existing values remain unchanged
```

```python
@property
def draft_answer(self) -> str:
    return "".join(self._answer_parts)

@property
def has_draft(self) -> bool:
    return bool(self._answer_parts)

def _freeze_partial_answer(self) -> None:
    answer = _compact_response(self.draft_answer)
    self._answer_parts = []
    if answer:
        self.entries.append(text_entry(DisplayKind.PARTIAL_AGENT, answer))
```

In `apply`, call `_freeze_partial_answer()` before status handling for `CANCELLED` and `ERROR`. Keep `ACTION_REQUESTED` as a clear-without-freeze boundary. Keep `_finish_display` as the only final-answer path.

- [ ] **Step 4: Run state tests**

Run: `uv run --with regex python -m unittest src.code_agent.interfaces.tests.test_terminal_state -v`

Expected: all tests pass.

- [ ] **Step 5: Update the Unit contract and commit**

Add to `src/code_agent/interfaces/AGENTS.md` Units:

```markdown
- `TerminalState.draft_answer`、`has_draft`：投影当前模型回合的临时正文 | 进程内状态 | 不进入会话消息、Evidence 或完成判定
- `DisplayKind.PARTIAL_AGENT`：标识取消或错误后固化的未完成回答 | 无副作用 | 不得按最终 assistant 消息处理
```

```powershell
git add -- src/code_agent/interfaces/AGENTS.md src/code_agent/interfaces/terminal_display.py src/code_agent/interfaces/terminal_state.py src/code_agent/interfaces/tests/test_terminal_state.py
git commit -m "增加 TUI 临时回答状态" -m "- 变更内容：公开流式草稿并区分未完成回答。" -m "- 变更原因：为动态尾部提供不污染会话事实的正文投影。" -m "- 验证情况：TerminalState 定向测试通过。"
```

### Task 3: Render a bounded assistant block in the live tail

**Files:**
- Modify: `src/code_agent/interfaces/terminal_tail.py`
- Modify: `src/code_agent/interfaces/tests/test_terminal_tail.py`
- Modify: `src/code_agent/interfaces/AGENTS.md`

**Interfaces:**
- Consumes: `assistant_draft: str`, terminal `width`, optional `terminal_height`.
- Produces: `render_live_tail_frame(..., assistant_draft: str = "", terminal_height: int = 30) -> LiveTailFrame`.

- [ ] **Step 1: Write failing layout tests**

```python
def test_live_tail_places_streaming_answer_above_composer(self) -> None:
    frame = render_live_tail_frame(
        "next", "running", 40,
        assistant_draft="first\nsecond",
        terminal_height=12,
        color=ColorMode.NEVER,
    )
    self.assertLess(frame.text.index("◆ 正在回答"), frame.text.index("╭"))
    self.assertIn("first", frame.text)
    self.assertIn("second", frame.text)
    self.assertGreater(frame.geometry.height, 4)

def test_streaming_answer_is_bounded_and_sanitized(self) -> None:
    draft = "\n".join(f"line {index}" for index in range(30)) + "\x1b[2J"
    frame = render_live_tail_frame(
        "", "running", 32,
        assistant_draft=draft,
        terminal_height=10,
        color=ColorMode.NEVER,
    )
    self.assertIn("…", frame.text)
    self.assertNotIn("\x1b[2J", frame.text)
    self.assertLessEqual(frame.geometry.height, 10)
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run --with regex python -m unittest src.code_agent.interfaces.tests.test_terminal_tail -v`

Expected: FAIL because the new keyword arguments are unsupported.

- [ ] **Step 3: Implement wrapping and geometry**

Add parameters to both `render_live_tail` and `render_live_tail_frame`, then use focused helpers:

```python
def _render_draft(value: str, width: int, max_rows: int, color: ColorMode) -> list[str]:
    if not value:
        return []
    rows = _wrap_plain(safe_text(value), max(1, width - 4))
    clipped = rows[-max_rows:]
    if len(rows) > len(clipped):
        clipped[0] = "… " + clipped[0]
    return [colorize("◆ 正在回答", BRAND_CYAN, color)] + [
        colorize("  " + row, BODY_WHITE, color) for row in clipped
    ]

def _wrap_plain(value: str, width: int) -> list[str]:
    rows: list[str] = []
    for logical in value.split("\n"):
        remaining = logical
        if not remaining:
            rows.append("")
        while remaining:
            part = clip_display(remaining, width)
            rows.append(part)
            remaining = remaining[len(part):]
    return rows
```

Compute `draft_budget = max(1, terminal_height - len(palette_items) - len(rows) - 5)`, prepend the draft lines to `lines`, and add their count to `cursor_row`. Preserve `_rewrite_tail` and `clear_live_tail` behavior.

- [ ] **Step 4: Run tail tests**

Run: `uv run --with regex python -m unittest src.code_agent.interfaces.tests.test_terminal_tail -v`

Expected: all tests pass.

- [ ] **Step 5: Update the Unit contract and commit**

Update the existing renderer Unit to state that `render_live_tail_frame` accepts a bounded provisional assistant block.

```powershell
git add -- src/code_agent/interfaces/AGENTS.md src/code_agent/interfaces/terminal_tail.py src/code_agent/interfaces/tests/test_terminal_tail.py
git commit -m "渲染有界流式回答区" -m "- 变更内容：在输入框上方安全换行并裁剪临时正文。" -m "- 变更原因：让文本增量可见且不改写终端滚动区。" -m "- 验证情况：TerminalTail 定向测试通过。"
```

### Task 4: Coalesce model-event redraws and finalize once

**Files:**
- Modify: `src/code_agent/interfaces/windows_tui.py`
- Modify: `src/code_agent/interfaces/tui_lifecycle.py`
- Modify: `src/code_agent/interfaces/terminal_renderer.py`
- Modify: `src/code_agent/interfaces/tests/test_windows_tui.py`
- Modify: `src/code_agent/interfaces/AGENTS.md`

**Interfaces:**
- Consumes: `TerminalState.draft_answer`, `DisplayKind.PARTIAL_AGENT`.
- Produces: `WindowsTerminalApp._request_redraw(immediate: bool = False) -> None`; animation interval `1 / 30` seconds.

- [ ] **Step 1: Write failing app tests**

Create an async gated engine so the draft can be inspected before completion:

```python
async def test_streamed_answer_is_visible_before_completion_and_finalized_once(self) -> None:
    release = asyncio.Event()

    class GatedEngine:
        async def run(self, *_: object, **__: object):
            yield AgentEvent(EventKind.MODEL_EVENT, {
                "event": ModelEvent(ModelEventKind.TEXT_DELTA, text="live text").to_dict()
            })
            await release.wait()
            yield AgentEvent(EventKind.MESSAGE_ADDED, {
                "message": Message(role="assistant", content="live text").to_dict()
            })
            yield AgentEvent(EventKind.COMPLETED, {})

    output: list[str] = []
    app = WindowsTerminalApp(AgentController(GatedEngine()), ApprovalBroker(), write=output.append)
    await app.submit("inspect")
    await asyncio.sleep(0.05)
    self.assertIn("live text", _plain("".join(output)))
    release.set()
    await app.wait_idle()
    self.assertEqual(_plain("".join(output)).count("◆ live text"), 1)
```

Add a cancellation test asserting the frozen partial label appears once and the text is absent from `state.transcript`.

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run --with regex python -m unittest src.code_agent.interfaces.tests.test_windows_tui -v`

Expected: FAIL because draft text is not passed to the live tail.

- [ ] **Step 3: Wire draft rendering and coalescing**

In `redraw` pass:

```python
size = shutil.get_terminal_size((100, 30))
frame = render_live_tail_frame(
    self.input.text,
    status,
    size.columns,
    assistant_draft=self.state.draft_answer,
    terminal_height=size.lines,
    # retain existing keyword arguments
)
```

In `_consume` and `_consume_task`, do not call `redraw()` for `MODEL_EVENT`; the animation loop performs the coalesced update. Keep `_flush_pending_entries()` and immediate `redraw()` for every non-model event. Change the animation interval:

```python
async def animate(app: object) -> None:
    while app._run_task and not app._run_task.done():
        app._spinner_index += 1
        if app.state.status == "running" or app.state.has_draft:
            app.redraw()
        await asyncio.sleep(1 / 30)
```

Style `PARTIAL_AGENT` with the normal Agent body color plus a trusted warning label; do not reuse success styling.

- [ ] **Step 4: Run interface and root TUI tests**

Run: `uv run --with regex python -m unittest discover -s src/code_agent/interfaces/tests -p 'test_*.py' -v`

Expected: all tests pass.

Run: `uv run --with regex python -m unittest tests.test_agent_app tests.test_command_integration -v`

Expected: all tests pass.

- [ ] **Step 5: Update Units and commit**

Update the `WindowsTerminalApp` and `tui_lifecycle` Unit lines to mention coalesced draft repainting and immediate non-model flushes.

```powershell
git add -- src/code_agent/interfaces/AGENTS.md src/code_agent/interfaces/windows_tui.py src/code_agent/interfaces/tui_lifecycle.py src/code_agent/interfaces/terminal_renderer.py src/code_agent/interfaces/tests/test_windows_tui.py
git commit -m "接通 TUI 正文流式显示" -m "- 变更内容：合并模型增量重绘并在完整消息后单次固化。" -m "- 变更原因：提供 Claude Code 式正文增长体验。" -m "- 验证情况：Interfaces 与根级 TUI 定向测试通过。"
```

### Task 5: Regression and Windows Terminal acceptance

**Files:**
- Modify only if results require a scoped correction: files already listed in Tasks 2–4.

**Interfaces:**
- Consumes: completed Tasks 1–4.
- Produces: verified independent streaming-TUI delivery.

- [ ] **Step 1: Run the complete automated suite**

Run: `uv run --with regex python -m unittest discover -s src/code_agent -p 'test_*.py' -v`

Expected: all Feature tests pass.

Run: `uv run --with regex python -m unittest discover -s tests -p 'test_*.py' -v`

Expected: all root integration tests pass.

- [ ] **Step 2: Run static syntax and diff checks**

Run: `uv run python -m compileall -q src/code_agent/interfaces`

Expected: exit code 0.

Run: `git diff --check`

Expected: exit code 0 and no output for this batch's files.

- [ ] **Step 3: Perform the Windows Terminal smoke test**

Run: `uv run chaos-agent`

Verify all four observations:

```text
1. A multi-paragraph answer grows above the composer before completion.
2. Resizing the terminal during generation keeps the input cursor inside the composer.
3. Existing scrollback remains selectable and is not repainted.
4. Completion removes the provisional block and appends one final Markdown answer.
```

- [ ] **Step 4: Commit only verified corrections, if any**

If Step 1–3 required corrections, stage only those exact files and commit:

```powershell
git commit -m "修正流式 TUI 回归问题" -m "- 变更内容：记录实际修正的测试或渲染边界。" -m "- 变更原因：满足 Windows Terminal 验收。" -m "- 验证情况：完整自动化回归与人工烟测通过。"
```

If no correction was needed, do not create an empty commit.
