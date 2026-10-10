# S10 Context consumers 实施方案

默认仍为 semantic；summary、boundary、persistent 是任务已冻结的显式兼容策略，不在本阶段更换其摘要与边界语义。消费者只经 Sessions 公共有界接口读取，不窥仓储实现，不在生产缺接口时退回全量读取。

构建读取 checkpoint/window metadata 的有界页及其未覆盖消息 suffix；最新真实 user 单独读取并保留。累计消息条数、解码字节和 checkpoint metadata 都有明确上限，必要原始 suffix 或完整工具组不能装入时明确失败，原始日志保持不变，不先加载大 blob 再截断。无 checkpoint 的旧超长日志也不得悄截尾冒称已覆盖；如需迁移先用显式兼容压缩，而非引入第二压缩算法。

来源第一次验证逐页增量计算旧格式 digest，保留首末锚点。缓存仅在同 thread/source identity 与可靠 message_epoch 不变时复用；append 不使历史前缀失效，UPDATE/DELETE、fork、rewind 或损坏必须重新验证并拒绝错误来源。每次验证前后核对 journal revision，禁止并发修改时发布来源有效的假结论。序号是全局游标，不当作条数，也不用 range 集合展开覆盖区间。

历史工具使用稳定 UUIDv5、SQL display JSON 片段及 bounded search/list，保持 Unicode 字符 offset 和既有 window 归属。旧兼容 context_history 的内容 casefold 搜索不能变成 display JSON 搜索。窗口 latest/request 与 usage totals 使用 S7 journal 的公开 latest/page/aggregate API；不新建账本。最终发送约束由 PreparedProviderRequest / RequestBudgetConstraints 负责，消费者不另建预算守卫。

实施文件限 thread_intelligence/context_builder 与小辅助、context_windows builders/history/tools 与小辅助及本 Feature 契约/邻近测试；必要 Host read_only_history 先与主 Agent 对齐。Sessions/Core 与 Provider DTO/计数分别由同阶段 agent 维护。

验收采用显式 TemporaryDirectory，并在构造 Repository 前断言路径归属。测试覆盖多窗口、仓储重开、同序号原文损坏、append 热缓存、fork/rewind、未闭合组与超大必要组、旧策略、Unicode 片段；600/6000 行测量 decoded rows/bytes、SQL 查询与 tracemalloc 峰值，区分首次来源流式扫描与后续 suffix 有界读取。无真实 Provider 调用或用户历史库读写。


实施补充：显式 compact_context 对 raw 行/字节容量失败以及真实 assembled prepared 请求容量失败都采用现有分批迁移。semantic 复用 SemanticCompactor，summary/boundary 复用 HandoffWriter，persistent 复用空 carry window。默认 build 不自动迁移超长原文；中断保留已发布前缀。source revision/CAS、exact ID+name、单完整组容量、真实 checkpoint、必要 prefix/user/carry 是停止条件。窗口完成前通过原策略恢复 bundle 和同一公开 Guard 预检，不发 main。compact_context 没有 tools 参数，真实带工具的发送仍独立重检。

builder 与 persistent remaining 采用 Guard.effective_input_cap；没有该公开方法的测试模型才保留 logical 兼容。正常 _rotate 选择真实可准入完整 prefix，cut=len(selected source)，未覆盖部分仍留 suffix。129 个 obsolete checkpoint 由 SQL 覆盖排除；window 无历史总128cap，metadata 页16流式遍历，仅保last2和完整journal摘要。metadata查询随历史window数增长，原文热解码有界。
