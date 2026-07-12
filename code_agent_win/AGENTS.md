# Windows Application Integration
组合各 Feature 成为 Windows TUI、CLI 和 JSON 模式共用的可运行代理。

## 边界
- 负责：创建共享依赖、调度 typed tools、把策略审批结果传递给文件和运行时 Feature。
- 负责：发布 Chaos Agent 的 `chaos-agent` 命令与本地数据目录，并在迁移期保持旧 `agent` 命令和本地状态可用。
- 不负责：重写 policy、workspace 或 runtime 的底层安全规则。

## Units
- `RootActionDispatcher`: 在执行前评估策略并请求交互审批，再调用 typed 文件、编辑、Git 或命令 Unit | 产生 tool result | 只有已通过策略的外部路径可抵达 workspace Unit
- `create_application(workspace_root, model_name, profile_name): Application`: 组合配置、策略、workspace、会话、provider、TUI 与 CLI 所需对象 | 创建新 Chaos Agent 本地目录，或读取旧 code-agent 会话 | 配置的访问级别控制外部路径与敏感路径能力
- `Application.foreground_tasks`: 组合前台任务控制器和同一会话/引擎实例 | 创建可恢复本地任务 | 不创建后台 daemon、worktree 或自动 Git 提交
