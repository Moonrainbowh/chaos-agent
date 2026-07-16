# Thread Intelligence
在完整保留原始会话的前提下，为超长线程提供可重建的语义压缩、检索、引用和 `read_thread` 上下文。

## 边界
- 负责：接收 Context Engine 的预算压力，在默认 90% 阈值附近对已闭合的旧消息区间生成有界语义 checkpoint。
- 负责：保留最近原文、最新用户意图及完整工具调用/结果配对；系统规则、任务契约、任务事实和 evidence 继续由原有可信通道单独渲染。
- 负责：为派生 checkpoint 保存稳定源消息范围、稳定 ID、digest、生成模型、用量和版本，使摘要可追溯、可丢弃并可重建，且始终保持为不可信上下文。
- 负责：构建有界线程索引，按当前 thread 和已授权子 thread 搜索消息、事件、摘要及 evidence 引用。
- 负责：`read_thread` 返回稳定来源锚点和有界原文，并检查相关的后续修订、替代或回滚；模型推断的冲突只能标为候选。
- 负责：语义压缩和检索调用服从取消、超时与累计预算，不得产生隐藏的免费模型调用。
- 负责：语义服务不可用、失败、超限或结果不合法时，稳定回退到 Context Engine 的确定性压缩。
- 不负责：覆盖或删除原始消息、把摘要作为唯一任务状态、生成 verification evidence 或决定任务完成。
- 不负责：直接调用具体 provider、管理 API key、决定总提示预算、实现会话数据库或渲染搜索结果。
- 摘要、检索结果和历史工具文本始终是不可信上下文，不得自动升级为已验证事实。
- 模型只能读取当前任务 thread tree 中明确授权的范围，不能通过任意 thread ID 访问其他会话。

## Units
- `anchor_message(thread_id, sequence, message): AnchoredMessage`：为原始消息生成稳定来源锚点和内容 digest | 无副作用 | 原消息变化会使 digest 失效。
- `DeterministicSummaryService.summarize(request, cancellation): SummaryResponse` / `render_bounded_source_summary(sources, max_tokens): str`：按来源顺序生成有界且不可信的确定性摘要 | 无 provider 或网络副作用 | 仅渲染公开消息内容与工具 action 名称，不读取独立隐藏推理字段。
- `SemanticCheckpoint.create(sources, response): SemanticCheckpoint`：固化摘要来源范围、模型、用量、版本和范围 digest | 无副作用 | checkpoint 始终是不可信派生上下文。
- `semantic_checkpoint_payload(checkpoint): dict`：输出稳定且可 JSON 序列化的 checkpoint 元数据 | 无副作用 | 排除摘要、原消息文本和来源集合。
- `SemanticCompactor.compact(thread_id, revision, messages, *, context_tokens, context_limit, target_tokens, cancellation): SemanticCompactionResult`：在上下文压力达到阈值时压缩闭合旧区间并保留最近原文 | 调用注入的摘要服务 | 接收 thread、revision 和取消边界；revision 是由调用方持有的身份/审计字段，本 Unit 仅验证且有意不写入 checkpoint、summary 或 prompt；取消向上传播，失败、超时、孤立工具消息或预算超限时使用确定性回退。
- `BoundedThreadIndex.add(entry): None`：在显式授权的 thread tree 内维护容量受限的来源索引 | 超限时淘汰最旧条目 | stable ID 冲突会被拒绝。
- `BoundedThreadIndex.search(query, ...): tuple[SearchHit, ...]`：对授权消息、事件、checkpoint 与 evidence 文本执行有界检索 | 无副作用 | 不接受任意 thread ID 越权查询。
- `ThreadReader.read_thread(anchor, ...): ThreadRead`：读取稳定来源及其后续替代、回滚和实际工具结果 | 无副作用 | 推翻仅标记为冲突候选，不改写原始事实。
