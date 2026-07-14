# Python 扩展规范

## 适用条件
适用于本仓库的 Python 3.10+ 源码、根级集成入口与测试。

## 目录约定
- Feature 源码：`src/code_agent/<feature>/`
- Feature 测试：`src/code_agent/<feature>/tests/`
- 集成入口与组合逻辑：`code_agent_win/app.py`、`code_agent_win/cli.py`、`code_agent_win/tools.py`
- 集成测试：`tests/`

## 命名规范
- 文件、函数和变量：`snake_case`
- 类和枚举：`PascalCase`
- 异步边界显式使用 `async` / `await`；阻塞文件系统操作经 `asyncio.to_thread` 调用。

## Unit 粒度
- 单个源码或测试文件不超过 300 行。
- 单个函数超过 50 行或同时承担解析、权限和副作用时必须拆分。
- 集成层只组合 Feature 的公开接口，不重写 Feature 行为。

## 构建与运行
- 开发：`python -m unittest discover -s tests -p 'test_*.py'`
- Feature 测试：`python -m unittest discover -s src/code_agent/<feature>/tests -p 'test_*.py'`
- 打包：`python -m build`
