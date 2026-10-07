# Python 扩展规范
适用本仓库 Python3.10+ 源码、根集成和测试。

## 结构与代码
- Feature `src/code_agent/<feature>/`，测试其 `tests/`；Host组合入口 `chaos_agent/`（app.py/cli.py及组合模块）；集成测试 `tests/`。集成只组合公开接口，不重写Feature行为。
- 文件/函数/变量snake_case，类/枚举PascalCase；异步边界显式async/await，阻塞文件I/O经asyncio.to_thread。
- 新文件以300行为目标；历史超长Unit局部修复不强拆，新增复杂职责/模糊边界再拆。函数50行是拆分信号，多重解析/权限/副作用职责必须拆，不为行数碎拆。

## 检查与运行
- 全量：项目`.venv`或 `uv run --locked python scripts/run_tests.py`，动态全部Feature+remote+根集成。套件默认600s，线程栈宽限2s，timeout124；普通失败/超时继续汇总，清理失败中止并列未运行，零发现失败；末尾JSON必须是真实数量。
- `suite_process.py`复用Windows Job；管道放行后才发现测试，超时输出末测试/线程栈，确认Job清空；临时进度只IPC，失败沿用结构化输出。
- Windows CI 显式 `--split-root-modules`：根集成按源模块分别受同一600s监督，先在Job放行后的子进程发现完整ID，组发现并集与实际运行ID核对；组明细另列，根仍为一个逻辑套件。普通失败/超时继续，清理失败即停并列未运行组；默认与其他平台不拆组，不改变数据库持久化或测试断言。
- Feature `python -m unittest discover -s src/code_agent/<feature>/tests -p 'test_*.py'`；根 `python -m unittest discover -s tests -p 'test_*.py'`；打包 `python -m build`。
- PowerShell多于3行、含嵌套引号或中文的Python必须落盘`.py`再执行；python-c只限轻量单行无嵌套引号探测。

## 展示与数据
- wide动效只刷新尾部，保持历史选择、中文列宽/光标；compact为明确有界手机视口，仅真实状态变化重绘；不加历史装饰动效。唯一主题验证窄屏、NO_COLOR、reduced motion；预览必须复用真实渲染且标示示例，离线预览不调用模型/工具。入口 `python -m code_agent.interfaces.theme_preview`，支持 --theme slate --animate / --html docs/ui-preview/index.html。
- Excel/CSV动态嗅探或显式声明表头/类型，不盲用header=None。图表用自适应网格，不混绝对像素/固定add_axes；强相关多指标上下共享X轴，不用倍率缩放双Y轴伪造重合。


接口细节与历史说明（非默认规则）：`docs/next-version/s4/reference/python-before.md`。本文件已保留必要约束；参考快照不覆盖当前规则。
