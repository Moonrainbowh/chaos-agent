# 终端外观验证记录

日期：2026-09-05。本次新增 Aurora、Ember、Mono 三套终端外观，默认使用 Aurora。

## 已完成

- 64 项定向检查通过：新主题、中文/窄屏几何、Markdown 转录、流式回答、NO_COLOR、动效控制及真实 TUI 启动→主题切换→退出路径。
- 新主题可通过 `:theme aurora|ember|mono` 直接切换；`CHAOS_THEME` 控制下一进程的默认主题，`:theme motion off` 和 `CHAOS_REDUCED_MOTION=1` 可关闭动效。
- Python 编译和 `git diff --check` 通过。新主题模块均不超过 300 行，函数不超过 50 行。
- 通过 setuptools 构建 wheel，并检查主题、动效与离线预览模块均在包内。当前环境缺少可运行的 `build` 前端，因此采用 `setuptools.build_meta.build_wheel` 直接构建；没有更改依赖或执行在线安装。
- `index.html` 使用真实 ANSI 渲染器导出的内容，可离线切换主题和查看交互状态；`three-themes.png` 是同一渲染输出的静态绘图，已目视检查，不是 Windows Terminal 截图。

## 回归边界

测试时同一工作区发生了并行编辑，涉及任务准备状态、终态、语言与 runtime/plugin Picker。这些改动被保留，以下是运行当时的证据，不代表后续编辑后的稳定结果。

- 全量脚本 `scripts/run_tests.py` 在 Interfaces 停止：395 项中 3 failures、1 error。涉及 workspace/context 状态文案、action 完成文案、plugin 命令路径及 runtime Picker 测试替身属性。
- 单独执行根集成套件：395 项中 4 failures、1 error。4 条断言期待 VERIFYING 而当前实现进入 WAITING_DECISION；1 条模型命令测试期待转录条目，而当前实现打开 Picker。
- 因此不能把当前整个工作区标为全量回归通过。详细输出保存在 `checks/`。
- 内置浏览器安全策略拒绝打开本地 `file:` 页面。没有绕过限制，未完成 HTML 的浏览器目视验收；原生终端中的手动选择、复制、滚动和实际显示器上的动画仍需交互验收。

## 重现

在仓库根目录使用当前 Python 环境：

```powershell
.venv/Scripts/python.exe -m unittest code_agent.interfaces.tests.test_terminal_themes code_agent.interfaces.tests.test_terminal_renderer code_agent.interfaces.tests.test_windows_tui_rendering code_agent.interfaces.tests.test_streaming_final_fixes tests.test_terminal_appearance_integration
.venv/Scripts/python.exe -m code_agent.interfaces.theme_preview --html docs/ui-preview/index.html
.venv/Scripts/python.exe -m code_agent.interfaces.theme_preview --theme aurora --animate
.venv/Scripts/python.exe scripts/render_theme_contact_sheet.py
```

预览中的对话、模型名称和用量均为标记清楚的示例，不调用 Provider，不创建真实任务或执行工具。背景和字体是展示参考，程序不会修改 Windows Terminal 的背景或字体配置。
