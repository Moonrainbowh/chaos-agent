# Chaos Agent 开发评估分发

运行分发 `chaos-agent` 不含评估模块。本独立 add-on 保留旧导入
`code_agent.evaluation`、`chaos_agent.continuity_*` 和
`chaos_agent.context_experiment_host`，不覆盖运行包的 `__init__` 或其他 Unit。
源码仍在原目录；构建 wheel 直接映射这些文件，sdist 自包含映射副本，
可在仓库外重新构建。标准源码测试仍由根 `scripts/run_tests.py` 发现。

```powershell
python -m build benchmarks
python -m pip install dist/chaos_agent-1.0.3-py3-none-any.whl
python -m pip install benchmarks/dist/chaos_agent_benchmarks-1.0.3-py3-none-any.whl
chaos-benchmarks selfcheck --fixture-version v1
chaos-benchmarks host-offline --arm A --output C:/Temp/new-owned-benchmark-output
```

输出目录必须不存在，由操作者显式指定。命令仅使用离线固定模型或参考实现，
不调用 API；离线分数不代表真实模型成功率。原 API 实验脚本保留在源码仓库，
须遵守其冻结、授权和预算门槛；本入口不提供 API 模式。

窗口统计 `window_metrics_version=2`：首窗使用稳定虚拟 ID，不创建 window 行；
`committed_windows` 始终报告真实持久行数（A=0，B/C/D=3）。旧 A 组曾错误期待
首窗持久行，因此保留的旧报告不重算、不与本口径混合比较。文件、隐藏验证、
可信证据、用量和实际切窗/重启检查保持原义。
