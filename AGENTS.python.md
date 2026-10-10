# Python 扩展规范
适用本仓库 Python3.10+ 源码、根集成和测试。

## 结构与代码
- Feature `src/code_agent/<feature>/`，测试其 `tests/`；Host组合入口 `chaos_agent/`（app.py/cli.py及组合模块）；集成测试 `tests/`。集成只组合公开接口，不重写Feature行为。
- 文件/函数/变量snake_case，类/枚举PascalCase；异步边界显式async/await，阻塞文件I/O经asyncio.to_thread。
- 新文件以300行为目标；历史超长Unit局部修复不强拆，新增复杂职责/模糊边界再拆。函数50行是拆分信号，多重解析/权限/副作用职责必须拆，不为行数碎拆。

## 检查与运行
- 父来源复核优先运行 `python -m unittest tests.test_parent_source_review tests.test_s16_source_completion tests.test_source_completion_contract` 与相关 Core 测试，追加范围遵循以下规则及用户明确约束。真实模型 A/B 是独立语义实验，不替代生产回归或升级 `verification_status`。
- 默认采用足以判断改动正确性的最小验证范围，优先单用例或单模块，保持开发简单快速；通过即停。扩展测试须对应实际影响、失败或具体疑点，不因文件数量、提交或流程形式扩大范围。仅文档、注释和排版改动检查内容或diff，不运行代码测试。
- 日常本地与CI默认只验证 Windows / Python 3.13 一个环境。执行前核验解释器版本与路径，不凭`.venv`目录名判断；不为普通改动自动重复运行其他Python版本或完整平台矩阵。
- 日常解释器补丁版本以根`.python-version`为准（当前3.13.7）；固定`uv==0.12.13`，通过`uv sync --locked --group dev`同步依赖，保留`uv.lock`，不要在普通验证中重解依赖。CI兼容矩阵显式指定Python版本，避免被日常版本文件覆盖。
- 日常CI入口`python scripts/select_ci_tests.py`根据PR base至实际checkout、push before至after选测；`--base <sha> --head HEAD --plan`只输出计划。明确文档白名单不跑产品测试；仅测试改动选所属套件/根模块，共享测试辅助对象走全量；已审查局部Unit按脚本中的显式映射加邻近集成。共享、未映射、基线缺失、新分支、删除根测试、检出提交不符或dirty工作区保守全量。执行复用既有监管与汇总，Windows全量拆根模块，其他平台不拆。发布/依赖/打包/平台检查用`workflow_dispatch`的`compatibility`选项主动触发五格矩阵及构建、干净安装；push/PR只运行日常job。
- 按改动影响选择验证范围：共享核心改动（结束条件、预算、授权、取消、持久化/恢复、上下文或跨Feature接口）在默认单环境跑全量；局部实现先跑直接受影响用例或模块，仅在接口或协作行为受影响时追加邻近集成；仅测试同步修正跑相关模块及实际失败环境，不因测试或文档修正重新跑整仓、打包和全部平台。
- 发布前或实际变更Python支持范围、依赖/打包及平台实现时，运行与影响相称的兼容检查；发布前完整矩阵为Windows Python3.10/3.13、Ubuntu3.10/3.13、macOS3.13，并包含构建和干净安装。日常push/PR不得无差别触发该完整矩阵；CI触发与范围选择应遵循本分层规则。
- 所选范围通过后，仅因新改动、实际失败或具体未决问题追加检查；保留失败物证，不通过加长等待、跳过断言或重复重跑直到绿来掩盖问题。
- 全量入口：使用已核验的Python3.13环境执行`python scripts/run_tests.py`（或显式`uv run --locked --python 3.13 python scripts/run_tests.py`），动态全部Feature+remote+根集成；“全量”指测试覆盖范围，不意味着多版本、多平台。套件默认600s，线程栈宽限2s，timeout124；普通失败/超时继续汇总，清理失败中止并列未运行，零发现失败；末尾JSON必须是真实数量。
- `suite_process.py`复用Windows Job；管道放行后才发现测试，超时输出末测试/线程栈，确认Job清空；临时进度只IPC，失败沿用结构化输出。
- Windows全量CI显式`--split-root-modules`：根集成按源模块分别受同一600s监督，先在Job放行后的子进程发现完整ID，组发现并集与实际运行ID核对；组明细另列，根仍为一个逻辑套件。普通失败/超时继续，清理失败即停并列未运行组；默认与其他平台不拆组，不改变数据库持久化或测试断言。该分组监管不要求局部测试启动所有根模块。
- Feature `python -m unittest discover -s src/code_agent/<feature>/tests -p 'test_*.py'`；根 `python -m unittest discover -s tests -p 'test_*.py'`；打包 `python -m build`。
- PowerShell多于3行、含嵌套引号或中文的Python必须落盘`.py`再执行；python-c只限轻量单行无嵌套引号探测。

## 展示与数据
- wide动效只刷新尾部，保持历史选择、中文列宽/光标；compact为明确有界手机视口，仅真实状态变化重绘；不加历史装饰动效。唯一主题验证窄屏、NO_COLOR、reduced motion；预览必须复用真实渲染且标示示例，离线预览不调用模型/工具。入口 `python -m code_agent.interfaces.theme_preview`，支持 --theme slate --animate / --html docs/ui-preview/index.html。
- Excel/CSV动态嗅探或显式声明表头/类型，不盲用header=None。图表用自适应网格，不混绝对像素/固定add_axes；强相关多指标上下共享X轴，不用倍率缩放双Y轴伪造重合。


接口细节与历史说明（非默认规则）：`docs/next-version/s4/reference/python-before.md`。本文件已保留必要约束；参考快照不覆盖当前规则。
