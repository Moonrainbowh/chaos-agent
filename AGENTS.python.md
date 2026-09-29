# Python 扩展规范

## 适用条件
适用于本仓库的 Python 3.10+ 源码、根级集成入口与测试。

## 目录约定
- Feature 源码：`src/code_agent/<feature>/`
- Feature 测试：`src/code_agent/<feature>/tests/`
- 集成入口与组合逻辑：`chaos_agent/`（入口为 `app.py`、`cli.py`，其余模块负责 Host、上下文、TUI 与 Workspace 组合）
- 集成测试：`tests/`

## 命名规范
- 文件、函数和变量：`snake_case`
- 类和枚举：`PascalCase`
- 异步边界显式使用 `async` / `await`；阻塞文件系统操作经 `asyncio.to_thread` 调用。

## Unit 粒度
- 新建文件以 300 行为目标；已有超长文件的小修复只改相关逻辑，不因历史行数强制拆分。新增复杂职责或修改导致边界进一步模糊时再按职责拆分。
- 单个函数以 50 行为拆分信号；同时承担解析、权限和副作用时必须拆分，不为凑行数拆碎自解释的逻辑。
- 集成层只组合 Feature 的公开接口，不重写 Feature 行为。

## 构建与运行
- 套件监管：`scripts/suite_process.py` 组合现有 Windows Job；子进程在管道放行后才发现测试，超时输出最后测试标识及线程栈，并确认 Job 清空。临时进度文件仅用于进程间传递，失败沿用现有结构化输出。
- 全量回归：`python scripts/run_tests.py`（动态发现全部 Feature 测试，最后运行根集成测试；`--suite-timeout` 配置每套件秒数，默认 300，另有 2 秒线程栈输出宽限；超时退出 124）
- 根集成测试：`python -m unittest discover -s tests -p 'test_*.py'`
- Feature 测试：`python -m unittest discover -s src/code_agent/<feature>/tests -p 'test_*.py'`
- 打包：`python -m build`

## 终端界面验证
- 主题预览：`python -m code_agent.interfaces.theme_preview --theme slate --animate`；离线示例不调用模型或工具。
- 可交互对照页：`python -m code_agent.interfaces.theme_preview --html docs/ui-preview/index.html`；页面必须复用真实渲染器输出并明确标记示例数据。
- 动效只刷新动态尾部，保持历史可选择、中文列宽与光标几何；唯一主题必须验证窄窗口、`NO_COLOR` 和 reduced motion。

## 脚本运行与命令行规范
- 文件优先（File-First）：严禁在 PowerShell 下使用 `python -c "..."` 执行超过 3 行、包含嵌套引号或中文字符的内联脚本；多行与复杂逻辑必须先落盘为 `.py` 文件（如脚本或临时目录），再以 `python <path>` 执行，彻底规避字符串转义、编码乱码与长度溢出。
- 探测轻量：`python -c` 仅允许用于无嵌套引号的单行版本或环境探测。

## 数据处理与可视化规范
- 表头防御：读取多工作表数据文件（如 Excel/CSV）时，必须动态嗅探或显式声明表头与类型，禁止无防护盲目假设 `header=None` 造成数值列被推断为 object 类型。
- 布局自适应：科学图表与数据看板必须使用自适应网格（如 `GridSpec` 配合 `constrained_layout=True`），严禁混用绝对像素或固定比例 `add_axes` 导致多环境导出时重叠溢出。
- 科学对齐：多指标强正相关对比时优先采用上下共享 X 轴子图（Shared X-Axis），严禁以固定倍率粗暴缩放双 Y 轴致使曲线完全重叠。
