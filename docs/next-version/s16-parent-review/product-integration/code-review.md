# 产品集成代码独立复核

审查者：`core_review_routing`（用户指定 GPT-6.1-sol / medium）。主 agent 另核对关键 diff、实际请求与持久证据。

审查 Host 配置注入、额外 client 生命周期、旧快照恢复、预算交集、child 排除，以及本目录真实运行 harness。没有遗留的已知产品代码阻断。

- `ContextAssembly` 显式携带复核模型；`engine_for` 在有 child lineage 时排除它。`for_child` 不继承复核工厂。
- Core 每次请求固定 client/model name；MODEL_STARTED、stream 和 usage 使用同一选择。旧快照无绑定仍用原模型；缺失或漂移的已绑定模型在请求事件与发送前拒绝。
- Host 首次保存快照即保存身份。复核共享原 sessions、thread、ledger 和 Host ceilings，容量、输出、timeout、retries 取交集。
- `RuntimeClients` 负责普通与复核 provider 的退休、失败清理和关闭；未使用的复核 client 不打开网络资源。
- 审查发现复核 profile 缺少 Anthropic effort 校验；已在候选冻结前补上 `validate_profile_reasoning`，并单独保存真实 `wire_effort`。
- 新集成测试初版误把异步退休当作同步关闭，并误解 transport 错误为平静 failed。已按既有运行时语义修正测试为等待实际关闭、抛出 `ModelStreamError` 与 durable `interrupted`；最终 5 项通过。

真实链路使用正式配置入口，没有安装实验 `model-variant/adapter.py`。一次真实运行中普通 GLM 主轮选错目标的模型行为，另见运行及语义报告；代码审查不能代替该验收结果。
