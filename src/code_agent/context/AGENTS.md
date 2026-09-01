# Context Engine
在明确的 token 预算内渐进加载项目规则和代码结构，并用进程内混合检索构造稳定且可压缩的模型上下文。

## 边界
- 负责：把附件 token 估算纳入消息预算；最新 user 附件预算不足时明确失败，不能静默丢弃；旧消息压缩只保留有界附件元数据，不读取 blob。
- 负责：首回合从完整持久 user Message 构建上下文，避免用纯文本 `user_input` 重建时丢失附件或重复消息。
- 不负责：解析附件内容、生成 Provider content blocks，或让语义 summarizer 接触附件 blob。
- 负责：稳定系统前缀、分层 `AGENTS.md` 规则发现、按需 repo map、相关文件选择和 token 预算。
- 负责：在进程内维护可增量更新、带 generation 的仓库事实快照，并从稳定快照生成本轮轻量 Repo Map。
- 负责：用 SQLite FTS5 对有界源码正文、路径和符号进行词法召回，并与精确路径/符号、依赖图和 touched files 信号做确定性融合；FTS5 不可用时保留现有结构化排序。
- 负责：保留近期原文的确定性压缩，并为后续实验性压缩策略提供接口。
- 负责：压缩接收请求的 `thread_id`、`revision` 与 cancellation，但绝不持久化 checkpoint。
- 不负责：发起模型请求、修改文件、运行命令或持久化完整会话。
- 不负责：将 Repo Index 持久化到磁盘，依赖文件监控器、外部搜索服务、embedding 模型或向量数据库。
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
- `RepoFileScanner.scan(path): RepoFileFacts`、`extract_python_semantics(...)`: 读取单个受保护文件并提取签名、有界检索正文、scope-aware Python 定义/import/use/config facts | 只读取指定文件 | 参数、局部变量、comprehension 和 shadowing 不得误标外部 exact 引用；二进制或解析失败降级为 path-only facts
- `resolve_semantic_graph(records)`: 从同一批 File Facts 解析 `import/reference/call/inherits/config` 直接关系并整体发布 | 无副作用 | relative/alias/src/re-export 可解析；star import、动态派发和歧义名称不得标 exact；配置关系绑定 namespace/key/provenance；不保存 `test_impact`
- `plan_repo_query(query): RepoQueryPlan`: 将不可信查询拆为有界字面 term、中文 trigram、短中文词和少量中英代码词汇别名 | 无副作用 | 通道和输入长度均有硬上限，不把原始输入拼入 FTS MATCH 语法
- `SQLiteRepoSearch.sync(previous, current)`、`rank(query)`、`close()`: 以事务方式增量维护进程内 unicode61/trigram FTS5 文件索引并返回有界候选名次 | 维护内存 SQLite 连接 | 短 ASCII/CJK n-gram 使用索引字段；Feature contract 只索引正向职责；FTS5、trigram 或查询失败时降级为结构化检索
- `rank_repo_entries(entries, query, touched_files, lexical)`: 用 RRF 融合词法、精确路径/符号、最强 Feature contract 范围、依赖图和 touched files 名次 | 无副作用 | 不混合不可比的原始 BM25 分值；测试/文档有稳定先验降权，路径稳定打破同分
- `RepoIndexService.snapshot_for_turn(): RepoIndexSnapshot`、`query_for_turn(query)`、`invalidate(paths)`、`close()`: 首轮有界建索引，之后仅刷新 dirty path；在同一更新边界内发布 generation 并查询对应 FTS 候选；空路径请求下一轮有界 inventory reconciliation | 非阻塞合并失效请求，发布单调 generation 的进程内不可变快照 | 初始化、reconciliation 和精确新增均严格保持 `max_files`，无变更时返回同一快照且不扫描文件系统；关闭释放内存索引，重启后重建
- `select_tiered_context(...)`、`render_tier_selection(...)`、`RepoMapViewCache`: 从不可变 generation 派生 L0/L1/L2、反向测试影响候选和稳定 deferred 清单 | 维护最多 64 项的 generation/backend/index-aware LRU，L0 正文不长期缓存 | 相同 snapshot/query/touched/budget 逐字节一致；module-level `path:line` 只给 bounded slice；Repo Context 始终标记 `UNTRUSTED_REPOSITORY_DATA`
- `RepoMapBuilder.build/render/render_with_metrics`: 兼容入口，组合共享 Repo Index 与分层 Request-derived Context | 首次或收到失效通知时刷新索引，并在发送 L0 前后校验 FileSignature | stale 时精确失效并最多重建一次；再次变化则 fail-closed，不发送跨代 L0/L1/L2；输出不超过 token 预算
- `estimate_attachment_tokens(ref)`、`message_tokens(message)`: 只按安全元数据保守估算文本/图片附件预算 | 无副作用 | 不读取 blob
- `DeterministicCompactor.compact(messages): CompactionResult`: 以确定规则压缩旧消息，同时保留最近消息、附件安全元数据与工具调用/结果配对 | 无副作用 | 最新 user 附件超预算明确失败，不依赖模型摘要
- `WorkspaceContextBuilder.build(request): ContextBundle`: 按规则、工具、任务状态、按需 repo map 和消息的动态预算组装稳定系统提示词及本地数值度量，可选执行 source-anchored semantic compaction，并始终以确定性压缩施加最终硬边界 | 在线程池中读取工作区 | 透传真实 thread identity、revision 与 cancellation；非项目上下文或问候不触发 repo map；Context 不持久化；规则超出 3,000 token 立即失败；度量不含提示、规则或源码；非空输入只追加一个 user 消息
- `render_task_state(state, token_budget): str`: 按优先级渲染有界持久任务事实 | 无副作用 | 事实在工作笔记之前，工作笔记始终标记为未验证
