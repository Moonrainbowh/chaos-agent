# Development benchmark distribution

目标：独立可安装评估 add-on，保持历史 import 与运行分发隔离。

边界：`setup.py` 只映射原 `src/code_agent/evaluation` 与 Host 评估模块；
`chaos_benchmarks/cli.py` 只组合离线 selfcheck 与真实 Host scripted arm，
不改变 grader、oracle、场景或生产任务，不提供 API 模式。
开发 wheel 不包含运行包的 `__init__`、生产 Unit 或测试；sdist 物化映射源码，
仓库外可重建，不依赖父目录。未知用户 build 资产不清理，build 使用拥有的临时目录。

构建：根 `python -m build benchmarks`；源码完整检查沿用 `scripts/run_tests.py`。
验证：wheel 成员/raw SHA、sdist 仓库外重建；干净 venv 先安装 runtime 再安装 add-on，
检查原 import 与 `chaos-benchmarks selfcheck`、`host-offline`，离线结果不算真实模型成绩。
