# S10 最终请求与新 Task 上下文身份实施自测

本子范围实现、自测完成，停止产品修改，待 Root 联合冻结、全量与新独立监督。显式 20 个产品/测试/契约路径见 `request-files.json`；Core/ContextWindows 契约与 Interfaces task_controller 内的 Root/历史子范围须由 Root 合并同一 S10 diff，不把这里的目录列表当作独立完整阶段提交。

## 最终行为

- Chat、Responses（含 Codex）、Anthropic、Google（含 Antigravity）、Pi 客户端公开 prepare_request/stream_prepared。Transport 在认证变换后冻结最终 UTF-8 JSON body，同一 bytes 供计数、发送、重试；headers/URL/敏感诊断值不进入计数或 repr。Provider 原有 stream 保持直接调用兼容，各新增委派边界同步关闭底层流。
- PromptTokenCounter.prepared 计数实际协议 JSON，包括参数字符串、实际 schema、系统/规则/消息、已完整性核验的文本附件包装；另加 256 协议保留。仍是本地估计，不是 usage。图片缺显式校准政策时由 guard 在零 HTTP、零预留下拒绝；既有直接 Provider stream 图片协议映射保留。
- BudgetedWindowClient 复用 S7 账本一次 admission/settlement；配置/API/Host/auxiliary input/auxiliary total 取最严格额度。reserve 使用实际 prepared 与 configured output 较大值，auxiliary total 减实际输出与 safety。构造额度和每次调用约束不能被放宽。fake models 具有明确 logical compatibility 标签，真实五个 adapter 均必须准备实际 payload，不悄退逻辑估计。
- 关闭与取消不会丢最后已知 usage；部分使用以 completed=False 结算，缺失使用保留 reservation；closing 使用可选公开 aclose，保留只有 AsyncIterator 的 fake model 协议兼容。Codex 删除远端 output 字段保留原协议行为，并提供明确 diagnostics，不宣称远端上限被强制实施。
- 新 TaskContract.context_selection 是版本化、字段白名单及 immutable JSON mapping，记录实际 strategy/policy/window/input/output/Host cap/safety/counter 版本，不保存任意 config 或凭据。Host profile_facts 新增第 9 项 canonical JSON，旧 4/8 项保留。恢复在 active_matches 前核对当前事实，漂移拒绝执行。
- 已 WAITING_DECISION 的 runtime/profile 恢复失败保留等待，不触发非法 self-transition；首次 CREATED 发现配置漂移允许直接进入 WAITING_DECISION，避免制造虚假的 RUNNING。

## 自测物证

均使用 candidate locked CPython 3.13.2，PYTHONPATH 显式指向主工作区 src/root。Provider 只使用 MockTransport；涉及数据库均本测试显式 temp 路径，构造前 assert 在临时目录中，没有默认历史库。

| 检查 | 实际结果 | 证据 |
| --- | --- | --- |
| Provider 全 feature | 98 PASS，1.072s | providers-prepared-final-with-aux.log |
| ContextWindows 全 feature（含同阶段消费者新测试） | 28 PASS，5.824s | context-windows-prepared-final.log |
| Host 任务冻结/恢复、runtime 安全、四策略装配、Root cap/aux 接线、生产 child 工厂/权限/cleanup | 29 PASS，15.255s | prepared-host-regression.log |
| 最后只改 Guard 对 optional public aclose 的兼容，复核 prepared 高风险集 | 8 PASS，0.031s | prepared-request-close-final.log |

关键反例：Unicode 参数与实际 schema 超限 → 零 HTTP、零 reserve；text/plain 真实 blob 验 hash 并发送实际文本；无图像政策拒绝；Codex/Antigravity 在认证后核算；retry body bytes 一致且只 1 admission；响应 early close 已关闭且部分用量 incomplete；真实 RuntimeContextFactory 四策略 → ContextAssembly.build → Responses adapter → MockTransport，最终包含规则/技能/schema，主+辅助 ledger 两次实际 charge 共 8（fake usage），高于 Host cap 的真实技能前缀四策略零 HTTP/零 admission。不是仅构造 counter 数字。

Task 冻结反例：真实 Host 四策略新任务持久化、不可变/JSON 往返；同名 active profile 的 policy/window/input/output/ENV Host cap 漂移均拒绝；白名单、版本与秘密字段拒绝；旧 8 facts 不补上下文身份；CREATED 首次和 WAITING 再次恢复漂移皆返回决定事件且不调用模型。

初次自测失败日志保留：一次 adapter 提取脚本误识别多行签名造成 SyntaxError，以及新增流委派未立即关闭底层；修复后旧 Provider 90 项与最终 98 项通过。新 freeze fixture 起初误用不存在的 create（实际 start），新 assembly fixture 起初混用 sync patch 与 async with；均为 fixture 错误并已改正，后续上述真实路径已通过。没有隐藏失败/跳过。

Scoped git diff --check 已通过；只有既有 Windows LF→CRLF 提示。没有推送、提交、live Host、用户 authentication 改动或真实 Provider 调用。

## 兼容与局限

- 此报告不代表 S10 DONE，Root 尚需所有子范围统一 full runner 与独立 PASS。最终 root/feature RuleLoader 6,000 上限核验由 Root 执行，未把已通过局部测试说成规则总额度已验收。
- 原 request-diagnosis/probe.json 是实施前 S9 状态捕获。probe.py 的“旧 guard 放行”断言在当前实现应改为拒绝，当前正式新测试使用零请求断言；不覆盖原反例证据。
- 旧 Task 未保存 context_selection，无法事后证明过去策略。按已配置兼容路径恢复，已有窗口 strategy 冲突由 builder 明确拒绝，不补造旧快照、不写用户配置。本次旧契约实测是同配置短历史兼容；不声称所有未知旧 profile 漂移均能识别。
- JSON/tokenizer/UTF-8 字节估计不是 Provider 实测；尚无真实模型质量比较或图像 token 校准。采用有界本地输出预留，不冒称 Codex 远端无字段时可强制输出；实际超额仍由 S7 usage 事实结算。没有因此关停既有 API。
