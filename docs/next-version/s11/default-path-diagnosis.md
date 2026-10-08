# S11 默认 Memory 入口静态诊断

本轮只读产品代码并编写本报告；未启动 Host、创建 Application、连接任何数据库或修改候选。行号对应诊断时工作区。

## 结论与实际链

已有 Sessions Memory CRUD 可复用，默认 semantic 没有 identity、query 或 injection 三层接线。显式 persistent 有检索与投影实现，但生产调用没有给它 project 身份，所以同样默认不可达。

1. `chaos_agent/app.py:53` 是真实 `create_application`；`:83` 从调用方 workspace root / cwd resolve，`:133` 创建既有 SessionRepository，`:135` 创建 ManagedWorkspaceRuntime，`:195` 组合 RuntimeContextFactory。
2. `chaos_agent/application_context.py:68` 创建统一 Guard + ContextAssembly；`:148` 根据冻结 profile 选择 managed 或默认 semantic；`:156` 调用 build_context_runtime。`:285` 以 root_for_thread 选择当前运行工作树，缺失时 hydrate，再取 Host 根。
3. `chaos_agent/context_runtime.py:70` 生产 semantic 组合 WorkspaceContextBuilder 与 ThreadAwareContextBuilder；`src/code_agent/thread_intelligence/context_builder.py:78` 从 durable history 取消息、执行原 compactor，再交给 inner。两者没有 MemoryRepository 查询。
4. `chaos_agent/managed_context.py:16` 有 memory_project_id / memory_user_scope_id / allow_user_memory 显式参数；`:32` 仅给 PersistentContextBuilder。RuntimeContextFactory 当前调用没有传这些参数。
5. `src/code_agent/context_windows/persistent_builder.py:46` 以当前输入或最新 user 查询 memory；`:84` 将参考文本投影 system；`:107` 无 memory_project_id 就返回空，`:113` 调既有 search_memories(limit=4)，`:115` 保留适用性标签。

## 稳定身份的真实来源

- ManagedWorkspaceRuntime.root_for_thread（`chaos_agent/workspace_runtime.py:106`）是运行 worktree，不是原项目身份。startup/hydrate（`:112` / `:150`）从持久任务 lineage 恢复绑定；`:154` bind_persisted_task 仍只缓存 worktree_root。
- 原项目来源由 lease 提供：`chaos_agent/workspace_preparation.py:45` 验证 prepared lease 后以 worktree.source_root/repository_id/worktree_root 创建 WorkspaceLineageRecord。这个 source_root 是 Host 创建工作树的输入，不由模型字符串选择。
- 本机任务 `chaos_agent/local_workspace_lineage.py:73` 将 source_root/worktree_root 都设为 root；repository_id 是 Git common_dir 的规范路径 SHA256（`:96`）。无 Git / 无 commit 时 local lineage 可以不存在。
- 已有 `IntegratedForegroundTaskController._task_source_root`（`chaos_agent/foreground_tasks.py:198`）优先 lineage.source_root，再用冻结 TaskContract.authorization.workspace_root。此方法是私有，不建议让新 Feature 调私有方法。
- `chaos_agent/mobile_catalog.py:18` 已有公共 source_root(thread_id)：task lineage → task authorization → remote-session checkpoint → parent relation，带 32 层/cycle 防护。可借鉴 parent 解析，但 checkpoint.metadata 字符串不能直接升级为新的 Memory 访问授权；应限制可信 Host 创建来源并与当前 Host 原项目核对。它目前还全量 list_checkpoints，若复用需返回有界 exact-label 读取。
- `chaos_agent/remote/catalog.py:24` 的 project_id 由规范 root 的 normcase SHA256[:24] 得到，可复用同一算法或提取公共 root identity helper。它属于导航 ID，不应允许模型提供任意同形字符串来选择 Memory scope。
- 搜索 `chaos_agent` / Sessions / Workspace 未发现持久 `origin_root` 字段；不要假定已有该字段。应明确新增的是 Host 能力返回值或服务中的 authoritative original root，而非第二套数据库元数据。

最小 Host 接口建议：构造一个 project Memory control/service 时固定 composer 的规范原项目根与 derived project scope ID；对带 thread 的访问，验证 lineage/source ownership + parent owner relation 都归此原项目。新会话无 task 时使用该 Host 固定身份；持久 foreign task / foreign relation 明确拒绝，不能 fallback 到当前 Host 身份。managed child 应继承父 Host 的 project capability，同时运行 root 必须属于授权 lineage；fork 不按 fork ID 建新 project scope。直接启动在任意 linked worktree 的 Application 要先识别真实 Git common_dir 所属项目，或用可信持久 lineage 恢复原 source root，不能仅 hash 当前工作树路径；无 Git 则规范原 workspace root 定义身份。不要解析目录名、branch 名、用户文本中的 project_id。

## 最小用户控制与既有 API

复用同一 SQLiteSessionRepository，workspace/rewind router 已以 `__getattr__` 透传 Sessions 公共方法（`chaos_agent/workspace_session_router.py:45`）。不要另建 Memory DB / 向量库。

- create_memory(scope_type, scope_id, kind, content, source_refs, origin, conditions, lifecycle, idempotency_key)：用户显式保存可 active，自动提取只能 candidate。
- list_memories(..., include_history, limit)、search_memories(project_id, query, user_scope_id, allow_user_scope, limit)、search_memory_diagnostics：scope 默认只 project、active，user 默认关闭。
- revise_memory(memory_id, ..., expected_revision)、set_memory_lifecycle(..., revision, lifecycle)：保持 CAS。
- delete_memory：已有 forget 内容指纹阻止原文自动复活。
- promote_memory：必须明确 user scope 授权和去项目化正文，不把原项目来源读取权限带过去。

新 Host control 在调用按 memory_id 操作的 CRUD 前必须确认该 ID/revision 属于此 project，而不是仅依赖全库 ID 查询。需 Sessions 提供 scope-bound 读取/修改或有界精确 lookup；不能 list(limit=200) 找不到就拒绝老条目。正文、kind、来源和conditions展示有界。

真实 TUI 路径已足够：`_command_specs.py:27` built_in_command_specs → CommandRegistry；`command_availability.py:6` 服务可达列表；`tui_commands.py:39` enum、`:98` name-kind map、`:123` instruction kinds；`tui_command_dispatch.py:21` → `tui_builtin_commands.py:19` → dedicated handler。新增 `/memory` service 与 kind/actions，save/list/search/show/revise/withdraw/delete 即可，用户字符串只选择动作/ID，不选择 project 身份。建议 actions 使用明确语法保留正文/conditions/source；CAS 错误显示重载要求。来源需标注用户显式输入与自动 candidate，不能伪成 verified。

组合位置：`chaos_agent/application_product.py:16` configure_product_controls 创建 control；`:58` configure_product_ui 传入；`chaos_agent/ui_composition.py:25` / ui_runtime_composition UiComposition 将 service 注入实际 WindowsTerminalApp；`chaos_agent/app.py:244` _finish 将 public Application.memory 暴露，供 TUI/其它前端复用。Memory service 共用 sessions，无单独连接或关闭生命周期；Application.aclose 原有资源关闭路径不需新增第二数据库关闭。

## 默认 semantic 注入建议

Host 提供唯一可信 project Memory projection capability，在默认语义构建前从原 request.user_input 或 durable 最新真实 user 产生有界 lexical query；不从规则/工具文本猜 query。以公开 builder wrapper 或 workspace 的显式 reference payload 接线，给原有预算构建器计算全部 token，并由 final prepared guard 最终校验；不要在已计数 ContextBundle 后追加字符串绕预算。

投影必须是有界、不可执行引用数据（ID/revision/kind/source/conditions/applicability/content），显式提醒当前用户规则优先、适用性需核对、不改变 permissions、task state 或 verification evidence。memory 正文不能拼接成工具定义/规则/执行授权；使用确定性 JSON 编码减少内容伪造标题/边界。未知条件保留 needs_check，冲突条件标 conflict/可排除；不把 lifecycle active 当成已验证真事实。Notes 是当前工作笔记、History 是原日志、Memory 是显式可跨会话复用条目，三者不互相覆盖或自动发布。

persistent 原实现与新默认投影避免双重召回：若统一 capability wrapper 覆盖四策略，则停用其旧独立投影；或仅默认 semantic 新入口、persistent 同一可信能力与公开 scope，保证一次查询/注入和同预算。不得引入第二执行 loop、自动 agent 或模型调用检索。

## 端到端验证路径

全程 TemporaryDirectory，创建 Repository 前 assert DB/storage/product 路径都位于该 temp；patch `chaos_agent.app._session_path`、`_product_state_root`、`_workspace_storage_path` 的真实符号，以及 runtime config/model factory 成 MockTransport / scripted Provider。不要调用默认 create_application 后再改 sessions。

1. 真 create_application(temp project A) → `application.tui.submit('/memory save …')`，验证实际 MemoryRecord scope/origin/revision；通过真实默认 Controller/TaskService 首次 model request 捕获 prepared HTTP JSON，确认投影存在且无 synthetic builder ID。
2. close/recreate 同 A、TUI 新会话 → 当前查询仍命中，旧会话 ID 不是 Memory scope；另起 project B 同共享测试 DB → list/search/ID revise/withdraw/delete 均不得访问 A，实际 prepared request 也不含 A。
3. 真 fork relation / managed lineage，source_root=A、worktree_root=隔离子根 → 仍同 A；直接 foreign persisted task 恢复 / model 输入伪 project ID 均拒绝。nested child 继承 frozen original project capability，不随 root 改变 scope。
4. revoke/revise/delete 后重启重新查询，旧 revision 不再注入；forget 内容不能自动 candidate 重建。CAS stale revision 拒绝；candidate/history不默认注入，user-scope保持 opt-in。
5. 非 ASCII、超长、伪规则/工具正文、条件冲突与预算不足：预算和 frozen authorization不变，真实 final prepared guard检查新引用；无 Memory 时原默认语义行为保持。

本报告没有跑测试；上述是待实现验证方案，不能算 S11 已通过。
