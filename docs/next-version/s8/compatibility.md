# S8 生产调用链与兼容边界

`chaos_agent.app.create_application` 是默认装配入口，ApplicationFactory 组合原 Core、Sessions、策略、工作区与 Foreground。TUI、CLI、手机 Host 继续复用这个控制器；Application.tasks 显式返回同一 foreground 服务，ACP 经 AcpTaskService 投影到它。TaskService 是窄协议而非第二个执行器。

Session 是持久消息线程/分支；Task 冻结目标、授权、运行配置并保存累计预算；Attempt 使用已有 execution instance/owner，不新增表或重置任务开销。ACP Session ID 稳定，checkpoint 只选择当前 Task/thread，Task/Sessions 才是生命周期权威。未终态继续走原恢复门，终态续聊继承消息创建新 Task。Workflow 仍为可重建投影。

旧替代装配 `app_factory.py`、`app_models.py`、`app_presentation.py`、`factory_host.py`、`factory_context.py`、`legacy_execution_budget.py`、`subagent_runner.py` 及只测旧工厂的 `test_retained_child_factory.py` 已精确移除。先迁移 typed parent、冻结只读权限、父共享预算与子 scope 同源测试到真实 ApplicationFactory；28 项生产/恢复/子清理回归通过。子 context 与 verification 同一 scoped Sessions 的测试显式提供 100k 子请求预算，验收 scope identity，未更改默认额度。

`rewind_sessions` 只移除无人调用的 build_engine/build_child_engine_factory，保留 CoordinatedSessionRepository、snapshot、mutation gate 与实际 write-side 装配。Python 生产/测试/脚本全量引用扫描确认旧模块/函数无调用；历史研究及旧阶段报告保留原事实，不据此保留替代生产链。

ContextAssembly 显式 builder/client/actions/snapshot；兼容直接 build/compact forwarding。build_context_runtime 的既有直接 builder 返回仍保留，生产要求 return_assembly=True；wire_managed_engine 只接受明确 assembly，不沿私有 wrapper 搜索。外部自定义工厂需实现这个明确组合契约。

授权语义沿用既有中央 policy：普通父任务 TaskAuthorization 的 allow 字段是当前工作区的自动信任授权，false 不授该信任，并非替代独立的人类审批模式。ACP session-all 等显式 permission scope 可以按原 policy 放行已识别非关键动作，但不重写 TaskContract、根路径、恢复门或子权限。子任务另经 RestrictedDispatcher 的冻结硬上限，不能由父/客户端 permission 扩张。独立监督最初将父 false 当硬deny的反例假设已撤回，失败原日志保留，不据此改动全局 policy。

本阶段不提交、推送、合并、发布或切换 live Host。S7 base tree 与 S8 binary patch 支持审查和恢复源文件；数据库、旧任务、用户数据及原 authentication 改动不在移除范围。完整套件与独立监督另行记录，局部通过不代表放行。
