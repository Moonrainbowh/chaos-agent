# 上下文选择：最终构建验证

本次保留既有 L0 预算优先、最多两个独立 anchor、任务相关路径先验，补最终构建证据和可选调试报告。未改变记忆检索、换窗策略、模型请求或 Harness 主流程。

实际路径：`WorkspaceContextBuilder._finish_sync` → `RepoMapBuilder.render_with_metrics` → selection cache → signature-checked L0 source read → `render_tier_selection` → 最终预算检查 → `ContextBundle`。预算前候选不能代表最终进入 Prompt 的正文。

## Token 字段

| 字段 | 含义 |
|---|---|
| `prompt_budget_tokens` | 当前构建层允许的 Prompt 上限；Workspace 为配置 max，managed window 为有效 input cap |
| `prompt_safety_tokens` | Workspace 的安全预留；managed cap 已处理预留，因此这里为 0，不重复扣减 |
| `prompt_estimated_tokens` | 最终系统文本估计 + 工具定义估计 + 最终消息估计（含工具调用和附件元数据预算），始终复用 Context 本地估计器 |
| `repo_context_budget_tokens` | 本轮实际分配给 Repo Context 的预算 |
| `repo_context_estimated_tokens` | 最终渲染 Repo Context 字符串的本地估计，不包括系统里的固定 Repository map 引导语 |
| `prompt_tokens` / `repo_map_tokens` | 保持原语义：Workspace 为分配预算；managed window 的旧 `prompt_tokens` 是其专用 counter 的估计 |

窗口专用 counter 可使用 tokenizer 或字节上界并含协议预留，所以旧 `prompt_tokens` 与新通用本地估计不必相等。窗口容量检查仍使用原 counter。所有这些字段都不是模型 API 返回的真实 Usage；没有调用模型来校准准确度。`prompt_estimated_tokens` 不是精确 tokenizer 计数。

Skills、交互模式、peer 系统文本追加时仅同步新估计；窗口最终替换消息后重新估计。这里不改变内容、检索或预算决策。

## 调试入口

```python
reports = []
builder = WorkspaceContextBuilder(
    config, rules, repo_map, compactor,
    debug_report=reports.append,
)
bundle = await builder.build(request)
report = reports[-1]
```

接收函数在构建工作线程内执行；默认不生成报告、不写文件。报告不加入 Prompt、不进入 ContextBundle 数值度量或持久事件。

- `rendered_repo_context` 是最终渲染原文，包含实际发送的转义源码。
- `selected` 逐项列出最终 L0/L1/L2、path、range、symbol、source / symbol_signature / docstring、reasons。`range` 保留渲染请求范围；模块片段到 EOF 提前结束时 `source_range` 表示实际正文范围。
- `decisions` 比较预算前有界候选和最终记录，给出 kept / downgraded / removed、选择原因和发生阶段。400 行保护单独标明；预算前阶段可能同时包含 tier 数量限制。
- `deferred` 是实际发送的后续读取目标，不能当作已发送源码。
- 极小预算 compact fallback 可能只有截断标识，报告保留实际文本并标记 fallback，不从中推测完整 L0。
- 候选范围是已有的最多 2/8/16 个 L0/L1/L2，不对整个仓库的所有检索结果进行 tracing。

报告度量对应 WorkspaceContextBuilder 完成时；外层窗口或 Skills 构建结束后，以其最终 `bundle.measurements` 为准。Repo Context 没有在这些外层重新选择。

## 已复现的最小修正

原模块回退强制优先有符号/Python 文件，导致排序已正确的 `documentation manual` 最终仍选择 `app.py`。现在只在文档任务中取消该额外优先，复用排序器已有关键词规则；普通源码任务的既有回退仍保留。恢复旧逻辑运行新增测试，文档场景实际失败，其他场景通过。

## 测试与示例

`tests/test_final_context_selection.py` 的 12 个测试全部调用最终 Workspace build，覆盖 L0 优先/降级、双明确文件、无 symbol 的模块片段、test/docs/source 排序、单 anchor、缓存、默认关闭、跳过 Repo、工具/压缩/Skills 计数、极小预算、400 行保护与 stale fail-closed。另在窗口既有两项最终构建测试中补了度量断言。

```powershell
$env:PYTHONPATH='src;.'
$env:PYTHONUTF8='1'
.venv/Scripts/python.exe -m unittest discover -s src/code_agent/context/tests -p 'test_*.py'
.venv/Scripts/python.exe scripts/run_tests.py
```

完整示例见 `context_selection_example.json`，它来自临时三文件仓库的真实本地 build，不是模型效果评测。任务 `repair target`：

| 层 | 最终文件/符号 | 内容 | 原因 |
|---|---|---|---|
| L0 | target.py:2–3 / target | `def target(): return helper()` 正文 | explicit symbol |
| L1 | helper.py:2–3 / helper | `def helper():` 签名 | outgoing call/import/reference |
| L2 | leaf.py:1–2 / leaf | 路径、范围和符号；不发送正文 | distance 2 dependency |

预算 20,000，安全预留 500，Prompt 本地估计 252；Repo 预算 4,000、估计 184。Repo 预算降低到 180 后，L2 被移除，L0/L1 保留；Repo 估计 138，Prompt 估计 206。上述值以本次保存的 JSON 为准。


## 本次修改清单

以下为本次修改；工作区原先已有的 repo_ranking.py、三项旧测试等改动不计入本次。

| 文件（相对仓库根目录） | 本次修改 |
|---|---|
| src/code_agent/context/_builder_support.py | 输出分离的预算与最终本地估计，保留旧字段 |
| src/code_agent/context/measurements.py | 共用估计函数与追加系统文本后的计数刷新 |
| src/code_agent/context/builder.py | 传入最终 Repo 文本估计，提供默认关闭的调试回调 |
| src/code_agent/context/repo_map.py | 仅在最终校验通过后生成选择报告，stale 不复用旧报告 |
| src/code_agent/context/repo_tiered_context.py | 保留有界预算前候选引用；文档任务回退尊重排序 |
| src/code_agent/context/selection_debug.py | 最终 L0/L1/L2、实际正文范围、选择原因、预算淘汰与降级报告 |
| src/code_agent/core/models.py | 仅扩充 ContextBundle 数值计数字段白名单 |
| src/code_agent/skills/registry.py | Skills 追加后同步新估计 |
| code_agent_win/runtime_extensions.py | 当前线程 Skills/交互模式追加后同步新估计 |
| code_agent_win/peer_context.py | peer 系统文本追加后同步新估计 |
| src/code_agent/context_windows/builder.py | 完成窗口组装后刷新新预算/估计字段 |
| src/code_agent/context_windows/persistent_builder.py | 完成 persistent 组装后刷新同一计数字段 |
| src/code_agent/context/tests/test_final_context_selection.py | 新增 12 项最终构建测试 |
| src/code_agent/context_windows/tests/test_windows.py | 既有窗口最终构建测试增加预算与计数断言 |
| src/code_agent/context_windows/tests/test_persistent.py | 既有 persistent 最终构建测试增加相同断言 |
| src/code_agent/context/AGENTS.md、src/code_agent/core/AGENTS.md、src/code_agent/skills/AGENTS.md、src/code_agent/context_windows/AGENTS.md、code_agent_win/AGENTS.md | 同步上述计数和调试契约 |
| src/code_agent/context/CONTEXT_VALIDATION.md、src/code_agent/context/context_selection_example.json | 验证说明及实际本地构建示例 |

## 本次实际验证结果

- 修改前 Context：165 项通过。
- 修改后 Context：177 项通过（含新增 12 项最终构建测试）。
- Context Windows：22 项通过；Core：117 项通过。
- Workspace/context/managed-context/peer 相关根集成测试：27 项通过。
- 25 个非 Interfaces Feature 套件：共 1,582 项，均成功结束，其中 Workspace 跳过 20 项（如本机无 symlink 权限）。
- Interfaces：准备阶段 Esc 测试单独运行 20 秒超时；整套带诊断运行也在该项停住。排除该文件 3 项后，501 项中有 1 项图标动画断言失败，单独复测同样失败（期望图标变化，实际两次都是 `●··`）。没有修改这些测试或界面实现。
- 根全量集成套件长时间没有继续输出，停止本次进程后改跑上述 27 项相关集成，全部通过。不能宣称根全量通过；最后输出涉及的 model completion 测试单独运行 1 项通过，因此不把它误报为已定位的失败点。
- `git diff --check` 通过。

因此本次上下文选择/计数专项验证通过，但“全仓库原有测试全部通过”未达成。没有以跳过界面问题代替全量验收，也没有为此扩围修复。

原始证据在仓库根目录的 `context-validation-tests.log`、`context-validation-remaining.log`、`context-validation-focused-integration.log`、`context-validation-interfaces.log`、`context-validation-interfaces-rest.log`、`context-validation-startup.log`、`context-validation-icon.log`。其它 `context-validation-*.log` 为本次定位过程记录；其中 workspace 诊断设置的 25 秒超时仅是诊断限时，正式 workspace 套件后来 457 项通过。
