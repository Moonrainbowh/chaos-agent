# 方案 A 终端外观验证

日期：2026-09-05。唯一默认外观为冷萃冰阶（Muted Slate）；旧主题切换已移除。

## 输入与阅读体验增量验证

- 增加深青灰输入底色、标题分隔线、UTF-8 5120 字节总输入上限。
- 本次 Interfaces 415 项通过；追加 9 项输入/分隔线/底色与启动集成测试通过，包含未标记粘贴超限排空。
- 本节之后的 2242 项全量回归是上一版主题的结果，本次不重复全量。

## 上一版主题验证

- 全量 `scripts/run_tests.py` 完成：25 个套件、2242 项测试，全部套件 OK，其中 20 项条件跳过；根集成 398 项通过。

- Interfaces 408 项测试通过：输入、流式输出、取消、审批、中文列宽、安全清洗等现有交互。
- 唯一主题专项覆盖运行态 8–160 列、4–24 行窗口，确保光标可见且不越界；旧主题命令被拒绝，旧环境偏好被忽略。
- 真实应用类的离线启动 → 关闭/开启动效 → 退出集成测试通过，无 Provider 调用。
- 静态 ANSI 输出图 `muted-slate.png` 已目视检查就绪、生成中、审批三种状态；快捷键对比度已提高。
- 变更 Python 文件均不超过 300 行，函数不超过 50 行；语法解析与 `git diff --check` 通过。

## 验收边界

- `index.html` 来自生产渲染器，可离线切换示例状态；仅有一个主题。
- 浏览器安全策略拒绝本地 file 页面，未绕过限制，HTML 浏览器目视验收未完成。
- 静态图不是原生终端截图；实际显示器上的选择、复制、滚屏及动画观感仍需手动验收。
- 程序不修改终端背景和字体；巡移光带仅表示活动，不表示完成百分比。示例模型、对话和用量不代表真实执行。

## 重现

在已安装项目依赖的 Python 环境中运行：

```powershell
$env:PYTHONPATH = "$PWD/src;$PWD"
python -m unittest discover -s src/code_agent/interfaces/tests -p 'test_*.py'
python -m unittest tests.test_terminal_appearance_integration
python -m code_agent.interfaces.theme_preview --animate
python -m code_agent.interfaces.theme_preview --html docs/ui-preview/index.html
python scripts/render_theme_contact_sheet.py
python scripts/run_tests.py
```
