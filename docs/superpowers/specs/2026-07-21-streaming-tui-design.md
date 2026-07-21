# Windows TUI 流式正文设计

## 状态

已于 2026-07-21 经用户批准。作为独立批次先于 Checkpoint Rewind 交付。

## 目标

- Provider 产生 `TEXT_DELTA` 时，让 Windows TUI 的正文随增量增长。
- 保持普通终端滚动区追加式、可选择、可复制，不引入 alternate screen。
- 完整 assistant 消息到达后，只向滚动区固化一次最终 Markdown。
- 保持原始 reasoning 不可见，工具、审批、输入区和状态栏现有语义不变。

## 非目标

- 不把未完成 Markdown 增量直接写入普通滚动区。
- 不实现全屏 TUI、鼠标 UI 或自有滚动历史。
- 不改变 Provider 协议、消息持久化、完成判定或验证证据语义。
- 不把工具调用前的临时文字伪造成持久 assistant 消息。

## 当前基线

- Provider 已产生 `ModelEventKind.TEXT_DELTA`。
- `TerminalState` 把文本保存在 `_answer_parts`，但没有公开临时正文投影。
- `WindowsTerminalApp` 对每个模型事件重绘 live tail，却只在非模型事件后固化新增 `DisplayEntry`。
- `terminal_tail` 当前仅渲染 Picker、输入框和状态栏。
- 完整 `MESSAGE_ADDED` 或 `COMPLETED` 最终把正文追加为普通 Agent entry。

## 设计决策

### 临时正文属于视图状态

`TerminalState` 增加只读的临时正文投影，而不把 delta 提前加入 `entries` 或 `transcript`：

- `_answer_parts` 继续保存当前模型回合的完整文本。
- `draft_answer` 返回经终端安全净化但未经完整 Markdown 排版的临时文本。
- `draft_revision` 在可见正文变化时递增，供 TUI 判断是否需要重绘。
- `has_draft` 明确区分空草稿和有效草稿。

临时正文不是会话消息、工具结果、验证证据或完成状态。

### Live tail 增加 assistant 区

`render_live_tail_frame(...)` 接收可选 `assistant_draft`：

```text
◆ 正在回答
  第一行临时正文……
  最近一行继续增长▌

╭────────────────────────────╮
│ › 输入区                    │
╰────────────────────────────╯
状态                                             模型 · token/s
```

- assistant 区位于 Picker 下方、输入框上方，并纳入 `LiveTailGeometry.height`。
- 临时正文按显示宽度安全换行，不执行不完整的表格、代码围栏或链接布局。
- 可见高度受终端高度与固定上限约束；超出时显示延续标记和最后若干行。
- `safe_text` 继续剥离不可信控制序列；ANSI 只能由本地样式生成。
- ASCII fallback 使用现有主题符号，不另建一套视觉语法。

### 合并重绘

每个 delta 都必须立即进入 `TerminalState`，但终端写入按约 30 FPS 合并：

- 消费事件时设置 `_redraw_dirty`。
- 已有动画循环负责在最短刷新间隔后调用 `redraw()`。
- 非模型事件、审批出现、最终消息和任务终态要求立即 flush。
- 测试使用可注入时钟或显式 flush，不依赖真实 sleep。

该机制减少高频小 delta 引起的闪烁和 Windows Terminal 输出放大，同时保留感知上的逐字增长。

### 最终固化

完整 assistant `MESSAGE_ADDED` 是优先的权威正文：

1. `_finish_display(message.content)` 生成一个最终 Agent entry。
2. 清空 draft。
3. `_flush_pending_entries()` 先清除旧 live tail，再向滚动区追加最终 Markdown 一次。
4. 重绘不含 draft 的输入区与状态栏。
5. 随后的 `COMPLETED` 不得重复添加相同正文。

没有 `MESSAGE_ADDED` 的兼容事件流仍可在 `COMPLETED` 时从 `_answer_parts` 固化最终正文。

### 工具调用、取消与错误

- `ACTION_REQUESTED`：清除当前模型回合草稿，不固化为会话消息；工具生命周期正常追加。
- 用户取消或任务中断：若存在草稿，以显式“未完成回答”样式固化一次，随后清空；该条目不进入会话消息、证据或完成判定。
- Provider/运行错误：同样保留有界的未完成回答，并追加现有错误条目。
- 新任务、恢复线程或新 `MODEL_STARTED`：不得继承上一回合草稿。
- `REASONING_DELTA`：永不进入 draft、滚动区、状态或恢复投影。

未完成回答使用新增的 `DisplayKind.PARTIAL_AGENT` 和本地生成的“未完成回答”标签，避免被误认作最终回答。

## 组件边界

### Interfaces Feature

- `TerminalState`：维护完整临时文本和最终 entry 去重；不做终端几何计算。
- `terminal_tail`：负责草稿换行、截断、几何和可信 ANSI；不决定消息是否最终完成。
- `WindowsTerminalApp`：负责脏标记、合并重绘、立即 flush 边界和滚动区固化。
- `terminal_renderer` / `terminal_display`：渲染最终与未完成回答的可信样式。

Provider、Core 和 Sessions 不需要协议变更。

## 测试设计

### TerminalState

- 连续 delta 可从 `draft_answer` 立即读取，但 `entries` 仍为空。
- 完整消息只固化一次并清空 draft。
- 仅有 `COMPLETED` 的兼容流仍生成一次最终回答。
- `ACTION_REQUESTED` 清空临时文字且不固化。
- 取消和错误生成明确的 partial entry，不污染 transcript 的会话消息语义。
- reasoning delta 永不可见。

### terminal_tail

- 草稿位于输入框上方且纳入几何高度。
- 宽字符、组合字符、换行、窄窗口和窗口变化保持正确光标位置。
- 超长草稿只显示有界尾部并带延续标记。
- 控制序列被净化。
- 清除上一帧时不触碰普通滚动区，不使用全屏清除序列。

### WindowsTerminalApp

- 多个 delta 在完成前产生可观察的临时帧。
- 最终正文在滚动区只出现一次。
- 合并重绘不会丢失 delta，非模型边界会立即 flush。
- 工具、审批、Picker、输入编辑、Spinner 和 token/s 与草稿共存。
- 取消、错误和关闭 TUI 后不遗留动态正文。

### 人工验收

- Windows Terminal 中观察长回答逐步增长。
- 生成中调整窗口宽度，正文与输入光标不乱位。
- 生成中选择、复制已有滚动区文本不被重写。
- 包含代码块、中文宽字符和多段落的最终 Markdown 只固化一次。

## 实施阶段

1. 需求：更新 `src/code_agent/interfaces/AGENTS.md` 的边界，不填写新 Unit。
2. 实现：在 Interfaces Feature 内按测试驱动完成状态、渲染和 App 内部协作 Unit，并更新 Units。
3. 集成：若根级组合无需新依赖，不修改 `code_agent_win`；仅运行现有入口集成测试。
4. 验证：定向 Interfaces 测试、根级 TUI 集成测试、完整回归与人工 Windows Terminal 烟测。

## 完成定义

- 用户在 Provider 输出期间能看到正文持续增长。
- 完成后普通滚动区只有一个最终 assistant entry。
- 未完成正文始终标记为 partial，不能成为持久会话事实或完成证据。
- 不破坏追加式滚动、输入、审批、工具状态、Unicode、颜色和窗口调整。
