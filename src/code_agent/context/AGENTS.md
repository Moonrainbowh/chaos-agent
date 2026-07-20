# Context Engine
在明确的 token 预算内渐进加载项目规则和代码结构，构造稳定且可压缩的模型上下文。

## 边界
- 负责：稳定系统前缀、分层 `AGENTS.md` 规则发现、按需 repo map、相关文件选择和 token 预算。
- 负责：在进程内维护可增量更新、带 generation 的仓库事实快照，并从稳定快照生成本轮轻量 Repo Map。
- 负责：保留近期原文的确定性压缩，并为后续实验性压缩策略提供接口。
- 负责：压缩接收请求的 `thread_id`、`revision` 与 cancellation，但绝不持久化 checkpoint。
- 不负责：发起模型请求、修改文件、运行命令或持久化完整会话。
- 不负责：第二阶段不将 Repo Index 持久化到磁盘，不依赖文件监控器或向量数据库。
- 不负责：将整个仓库或所有扩展说明无条件注入提示词。
- 负责有界渲染目标、未满足 required criteria、当前 generation 与最新有效 evidence 摘要；已失效证据和模型工作笔记不得作为通过事实呈现。
- 不负责：解释原始 verifier 全量输出、生成 evidence 或决定完成状态。

## Units
- `render_evidence_summary(...)`: 渲染当前 generation 的 required criteria 和有效 evidence 摘要 | 无副作用 | 优先保留失败/未满足条件，绝不输出完整 verifier 原始内容
- `PromptBudget.allocate(system_and_rules_tokens, tool_tokens, task_state_tokens): PromptAllocation`: 在固定安全余量下为规则、工具、任务状态、repo map 和消息分配 token | 无副作用 | repo map 先于消息收缩，保留最小消息预算
- `ContextConfig`、`ProjectRule`、`Symbol`、`RepoEntry`、`CompactionResult`: 冻结上下文构建配置和中间结果 | 无副作用 | 路径和预算在构造时校验；旧 map/message 预算参数归一化为 `PromptBudget`
- `estimate_tokens(text): int`、`truncate_to_tokens(text, budget): str`: 对 ASCII、多字节字符和代理对做确定性保守估算与截断 | 无副作用 | 不切断 Unicode 代理对
- `RuleLoader.load(cwd): tuple[ProjectRule, ...]`: 按根规则、根目录直属扩展规则和目录链加载受边界保护的说明 | 读取已授权工作区文件 | 以根目录 mtime 复用直属扩展名称，不递归扫描工作区；严格受单文件和总字节预算约束
- `RuleLoader.render(rules): str`: 把规则序列编码为稳定、带路径边界的系统提示片段 | 无副作用
- `RepoFileScanner.scan(path): RepoFileFacts`: 读取单个受保护文件并提取签名、Python import、AST/常见语言声明 | 只读取指定文件 | 二进制、语法错误和不支持语言降级为 path-only facts，不是完整语言服务器
- `RepoIndexService.snapshot_for_turn(): RepoIndexSnapshot`、`invalidate(paths)`: 首轮有界建索引，之后仅刷新 dirty path；空路径请求下一轮有界 inventory reconciliation | 非阻塞合并失效请求，发布单调 generation 的进程内不可变快照 | 初始化、reconciliation 和精确新增均严格保持 `max_files`，无变更时返回同一快照且不扫描文件系统；重启后重建
- `RepoMapViewBuilder`、`RepoMapViewCache`: 从不可变快照按 query、touched paths 和 token budget 排序、裁剪并缓存本轮视图 | 维护最多 64 项的 generation-aware LRU | 只查询已有 Index，不读取工作区
- `RepoMapBuilder.build/render/render_with_metrics`: 兼容入口，组合共享 Repo Index 与轻量 Turn Repo Map | 首次或收到失效通知时刷新索引 | 同一 generation/query/touched/budget 复用视图；输出不超过 token 预算
- `DeterministicCompactor.compact(messages): CompactionResult`: 以确定规则压缩旧消息，同时保留最近消息与工具调用/结果配对 | 无副作用 | 不依赖模型摘要
- `WorkspaceContextBuilder.build(request): ContextBundle`: 按规则、工具、任务状态、按需 repo map 和消息的动态预算组装稳定系统提示词及本地数值度量，可选执行 source-anchored semantic compaction，并始终以确定性压缩施加最终硬边界 | 在线程池中读取工作区 | 透传真实 thread identity、revision 与 cancellation；非项目上下文或问候不触发 repo map；Context 不持久化；规则超出 3,000 token 立即失败；度量不含提示、规则或源码；非空输入只追加一个 user 消息
- `render_task_state(state, token_budget): str`: 按优先级渲染有界持久任务事实 | 无副作用 | 事实在工作笔记之前，工作笔记始终标记为未验证
