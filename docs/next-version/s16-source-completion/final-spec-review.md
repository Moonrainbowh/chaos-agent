# 累计规格独立审查

对象：基线 `afbd8f3` → 候选 `98f9a744650890c69a305ae643443b384006123e`。结论：**P0–P3完成门规格符合，PASS_P3_SPEC；整体阶段仍待Standards违规修复，P4/S16仍pending。** 独立Python3.10回归50项及supervisor13项通过，详证见p3-independent-review.md与两个独立日志。

- **(a) 缺失/部分要求。** P0–P3未发现代码缺失。“先完成零 Provider 的公开入口预检……实际运行一次”及“最终候选准备交付时核对五平台 CI”尚待P4/Root最终验证，这是阶段pending，不能称全部已完成。
- **(b) 范围扩张。** 未发现未授权扩围。“新任务以已有冻结 agent_topology=team 选择至少 STANDARD”及空答复/最终advisory关联修复已写入批准规格；hard额度、child不能续父租约、已有预算不迁移保留。没有LLM judge、high路由、新状态平台或Provider重试至通过。
- **(c) 已实现但错误。** 当前未发现阻塞反例。“只认当前子线程的成功读取回执和完整文本结果”“反馈后……未增加必要来源证据……source_requirements_unmet”“来源读齐且已有最终答复”均经真实diff及针对性运行核对。旧v7自然3child请求返回准确来源失败，新P2自然父最终文本确实发送；语义、引用及用量仍独立验收。

其他审查轴确认Guard文件系统I/O在async路径直接执行，违反“阻塞文件I/O经asyncio.to_thread”；认可该硬违规，整体放行须等待Root批准修复及新候选复验，本规格PASS不覆盖慢文件系统下取消响应。现有diff-check因原日志尾空格/测试EOF空行非零，不误报通过。完整证据见p3-independent-review.md。此结论不外推真实模型成功率、P4或S16整体通过。

## 三处I/O增量复核（工作树，尚未提交）

已只读核对 `98f9a74` 后两处Host文件及新增测试：`child_runner`的来源规范化、`SourceCompletionHost.snapshot`的传入来源规范化与成功回执路径检查，均将原同一同步函数移入 `await asyncio.to_thread(...)`。冻结/绑定顺序、可信回执身份、全量历史扫描、epoch核验及缓存提交逻辑未变；没有Core、Sessions或预算差异，未改变来源门或任务终态语义。新增两项反例分别核对真实Guard在线程内运行并继承ContextVar，以及取消检查后不提交缓存、下次从durable回执重放；工作线程只计算读取事实，不写状态。

结论：**未发现新的Spec阻断，PASS_P3_SPEC保留。** 本节仅覆盖未提交增量；原50+13项证据仍只对应98f9a74，不作为增量运行证明。I/O定点复验由Standards审查者承担；新候选公开preflight与P4仍pending。
