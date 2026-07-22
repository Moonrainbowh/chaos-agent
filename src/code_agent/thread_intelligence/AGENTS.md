# Thread Intelligence
在完整保留原始会话的前提下，为超长线程提供可重建的语义压缩、检索、引用和 `read_thread` 上下文。

## 边界
- 负责：接收 Context Engine 的预算压力，在默认 90% 阈值附近对已闭合的旧消息区间生成有界语义 checkpoint。
- 负责：保留最近原文、最新用户意图及完整工具调用/结果配对；系统规则、任务契约、任务事实和 evidence 继续由原有可信通道单独渲染。
- 负责：为摘要保存源消息范围、稳定 ID、digest、生成模型、用量和版本，使摘要可追溯、可丢弃并可重建。
- 负责：从 Sessions 的稳定消息序号重建上下文，并在达到阈值时协调当前冻结任务 profile 的 `SemanticCompactor`。
- 负责：构建有界线程索引，按当前 thread tree 的授权范围搜索消息、事件、摘要及 evidence 引用。
- 负责：`read_thread` 返回稳定来源锚点和有界原文，并检查相关的后续修订、替代或回滚；模型推断的冲突只能标为候选。
- 负责：语义压缩和检索调用服从取消、超时与累计预算，不得产生隐藏的免费模型调用。
- 负责：语义服务不可用、失败、超限或结果不合法时，稳定回退到 Context Engine 的确定性压缩。
- 不负责：覆盖或删除原始消息、把摘要作为唯一任务状态、生成 verification evidence 或决定任务完成。
- 不负责：直接调用具体 provider、管理 API key、决定总提示预算、实现会话数据库或渲染搜索结果。
- 摘要、检索结果和历史工具文本始终是不可信上下文，不得自动升级为已验证事实。
- 模型只能读取当前任务 thread tree 中明确授权的范围，不能通过任意 thread ID 访问其他会话；根 thread 可读自身与直接子 thread，子 thread 只可读自身与父 thread，兄弟 thread 不互读。
- `search_threads` 与 `read_thread` 的调用方身份由 Host 注入，模型和插件不得提交或覆盖 caller thread ID。
- Plugin mode 或 custom Agent 只能移除线程工具，不能扩大授权范围；语义失败时保留现有确定性压缩结果。

## Units
- `ThreadAuthorization.authorized_threads(caller_thread_id)`、`ensure_can_read(...)`：只从持久化两级 thread tree 计算读取范围 | 读取关系存储 | 根可读直接子、子只可读父，兄弟和无关 thread 拒绝
- `anchor_message(thread_id, sequence, message): AnchoredMessage`：为原始消息生成稳定来源锚点和内容 digest | 无副作用 | 原消息变化会使 digest 失效。
- `DeterministicSummaryService.summarize(request, cancellation): SummaryResponse` / `render_bounded_source_summary(sources, max_tokens): str`：按来源顺序生成有界且不可信的确定性摘要 | 无 provider 或网络副作用 | 仅渲染公开消息内容与工具 action 名称，不读取独立隐藏推理字段。
- `SemanticCheckpoint.create(sources, response): SemanticCheckpoint`：固化摘要来源范围、模型、用量、版本和范围 digest | 无副作用 | checkpoint 始终是不可信派生上下文。
- `SemanticCompactor.compact(...): SemanticCompactionResult`：在上下文压力达到阈值时压缩闭合旧区间并保留最近原文 | 调用注入的摘要服务 | 取消向上传播，失败、超时、孤立工具消息或预算超限时使用确定性回退。
- `ThreadAwareContextBuilder.build(...)`: 从 Sessions 稳定消息记录协调语义压缩并在发布成功后委托现有 ContextBuilder | 摘要调用与 SQLite I/O | 发布失败使用原始消息，使下游继续确定性压缩
- `BoundedThreadIndex.add(entry): None`：在显式授权的 thread tree 内维护容量受限的来源索引 | 超限时淘汰最旧条目 | stable ID 冲突会被拒绝。
- `BoundedThreadIndex.search(query, ...): tuple[SearchHit, ...]`：对授权消息、事件、checkpoint 与 evidence 文本执行有界检索 | 无副作用 | 不接受任意 thread ID 越权查询。
- `ThreadReader.read_thread(anchor, ...): ThreadRead`：读取稳定来源及其后续替代、回滚和实际工具结果 | 无副作用 | 推翻仅标记为冲突候选，不改写原始事实。
- `ThreadIntelligenceTools.definitions`、`search(caller_thread_id, ...)`、`read(...)`：暴露不含 caller 字段的 provider schema，并用 Host 注入身份裁剪搜索和读取 | 读取索引存储 | Plugin mode/Agent 只能移除工具，不能扩大 scope
