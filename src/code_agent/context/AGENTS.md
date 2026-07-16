# Context Engine
在明确的 token 预算内渐进加载项目规则和代码结构，构造稳定且可压缩的模型上下文。

## 边界
- 负责：稳定系统前缀、分层 `AGENTS.md` 规则发现、repo map、相关文件选择和 token 预算。
- 负责：保留近期原文的确定性压缩，并为后续实验性压缩策略提供接口。
- 负责：压缩接收请求的 `thread_id`、`revision` 与 cancellation，但绝不持久化 checkpoint。
- 不负责：发起模型请求、修改文件、运行命令或持久化完整会话。
- 不负责：将整个仓库或所有扩展说明无条件注入提示词。
- 负责有界渲染目标、未满足 required criteria、当前 generation 与最新有效 evidence 摘要；已失效证据和模型工作笔记不得作为通过事实呈现。
- 不负责：解释原始 verifier 全量输出、生成 evidence 或决定完成状态。

## Units
- `render_evidence_summary(...)`: 渲染当前 generation 的 required criteria 和有效 evidence 摘要 | 无副作用 | 优先保留失败/未满足条件，绝不输出完整 verifier 原始内容
- `PromptBudget.allocate(system_and_rules_tokens, tool_tokens, task_state_tokens): PromptAllocation`: 在固定安全余量下为规则、工具、任务状态、repo map 和消息分配 token | 无副作用 | repo map 先于消息收缩，保留最小消息预算
- `ContextConfig`、`ProjectRule`、`Symbol`、`RepoEntry`、`CompactionResult`: 冻结上下文构建配置和中间结果 | 无副作用 | 路径和预算在构造时校验；旧 map/message 预算参数归一化为 `PromptBudget`
- `estimate_tokens(text): int`、`truncate_to_tokens(text, budget): str`: 对 ASCII、多字节字符和代理对做确定性保守估算与截断 | 无副作用 | 不切断 Unicode 代理对
- `RuleLoader.load(cwd): tuple[ProjectRule, ...]`: 按根规则、根扩展规则和目录链加载受边界保护的说明 | 读取已授权工作区文件 | 严格受单文件和总字节预算约束
- `RuleLoader.render(rules): str`: 把规则序列编码为稳定、带路径边界的系统提示片段 | 无副作用
- `RepoMapCache.get_or_scan(path, scan): RepoEntry`、`counters(): (hits, misses)`: 按文件大小和纳秒级修改时间复用成功扫描结果并提供进程内计数 | 维护最多 5,000 项的 LRU 缓存 | 读取、二进制或解析失败不缓存；重启后清空
- `RepoMapBuilder.build(query, touched_files): tuple[RepoEntry, ...]`: 在有界扫描内提取 Python AST 与常见语言声明并按关联性排序 | 读取工作区文件 | 不是完整语言解析器；缓存不跳过当前查询重排
- `RepoMapBuilder.render(query, touched_files, token_budget): str`: 渲染并截断代码地图 | 无副作用 | 不超过 token 预算
- `DeterministicCompactor.compact(messages): CompactionResult`: 以确定规则压缩旧消息，同时保留最近消息与工具调用/结果配对 | 无副作用 | 不依赖模型摘要
- `WorkspaceContextBuilder.build(request): ContextBundle`: 按动态预算组装规则、工具、任务状态、repo map 与消息，可选执行 source-anchored semantic compaction，并始终以确定性压缩施加最终硬边界 | 在线程池中读取工作区 | 透传真实 thread identity、revision 与 cancellation，active cancellation 会竞速并回收语义工作；Context 不持久化；规则超出 3,000 token 立即失败；语义度量仅含触发、fallback 与 source count 数值；非空输入只追加一个 user 消息
- `render_task_state(state, token_budget): str`: 按优先级渲染有界持久任务事实 | 无副作用 | 事实在工作笔记之前，工作笔记始终标记为未验证
