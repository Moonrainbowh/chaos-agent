# S13 独立监督

状态：WAIT_FINAL_CANDIDATE。第二候选 patch `9ffea9a2ae7389a8a440e2f9627115643ed9f75751a1e0e5950de0fd3bbc6fb6` 已通过下列已执行独立检查，但末轮具体疑点确定 token-only 取消窗口，故不能放行。主 Agent 已在 main 最小修复，待第三候选正式冻结与标准外层 summary。第二候选的 15 路径 main/candidate raw 与 patch 当时逐一匹配；S12 未被本阶段替换的 46 路径保持原字节；authentication 三文件 rawdiff 保持 `7cbeb87002763fbe5de0bf33a4fd68193af6d66e5340be75f2a1edeb776befca`。实际导入均为 candidate 源码，详见第二候选 `supervision-preflight.json`。首候选 `2a5961f2626eff6d54de323b5fc45b88d5e396bef003f3be257b0c920465420b` 因下列反例撤回，不能使用其未完成全套日志代替第二候选。

## 第二候选正式独立结果

- MCP 全部 27 项 / 18.878s PASS，`supervision-mcp-final.log`。真实官方 SDK 覆盖启动失败、握手/发现/调用故障、闲置和调用后退出、跨 Task 关闭、启动/调用重复取消、pre-yield startup deadline、短关闭预算与回收、watchdog fallback failure/timeout；本地风险覆盖、旧 generation/epoch、排队拒绝和容量分类均核验。
- Root 57 项 / 1.415s PASS，`supervision-host-final.log`：schema、源/目标中央审批、旧插件与 MCP generation、实际 owner 队列前重核、业务错误、Plugin control/live wiring、Rewind、应用和命令集成。
- S12 既有独立 78 项 / 29.060s PASS，`supervision-s12-final.log`：显式临时库构造保护、durable approval/digest/owner/TTL/state、重复取消、HTTP request/response/permission/status、Foreground/read-only history 替换竞态。接受部分仍未验证且无文件；Foreground verification 的物证仍是 system_planner，不是模型文本或工程证明。
- 自建 6 个正式 probe subprocess 均 exit 0，`supervision-probes-final.json` 绑定该 SHA。排队插件撤销和 MCP republish 拒绝旧动作、效果只 [0]，后续 [0,2] 且连接/目录保留；busy 仅拒第三请求，代际/目录/风险保留，第二请求完成 effects=[0,1]；真实 SDK shortclose 0.204s 返回、child 已消失、无需 probe 补救；fallback 失败无未观察 Task 异常，startup/close 均 fault=true、error_type=RuntimeError、ready=false；独立真实 Root→SDK schema/business-isError/后续调用链通过并清掉 child。
- 11 条生产规则链独立加载，无用户 DB，全部不超过 6000，总提示上限 20000；`supervision-rule-chains.json`。

## 已复现问题

原始红物证分别保留于 `supervision-queue-red.json`、`supervision-busy-red.json`、`supervision-close-budget-red.json`、`supervision-watchdog-error-red.json`；对应 `*-probe.json` 为第二候选正式绿结果，不能混用。

1. **已修复并独立重验：排队插件撤销窗口。** Root 的 source/schema/generation 检查最初只在入队前执行，首调用挂起期间第二插件请求排队、源撤销后仍实际调用。独立真实 Controller→Manager→Owner/Root 隔离内存适配器红证据为 effects=[0,1]、第二非错误。第二候选把检查传到 owner 真正调用前，并用专用 McpBeforeCallError 保留健康连接；两种排队反例正式通过。
2. **已修复并独立重验：队列满被误记为协议故障。** 首候选第一请求执行、第二请求排队时，第三请求在调用 SDK 前因容量满抛 RuntimeError；Controller 撤下目录/风险并增加 generation，尽管 Manager 仍 ready=true。第二候选专用 McpBusyError 明确 SDK 尚未调用，保留目录/风险/代际及原第二请求；独立 probe 正式通过。
3. **已修复并独立重验：短关闭预算遗留真实子进程。** 首候选 Manager 合法配置 start=2s、call=1s、close=0.2s，真实官方 SDK 服务在关闭 stdin 后不退出；close 在 2.411s 返回 TimeoutError，自有 child 仍活，红 probe 只能精确 PID+临时根 kill 清理。第二候选 owner close watchdog 用同 adapter 持有的 SDK process handle 终止，仅原 owner 退出 context；0.204s 返回且 PID 已消失。默认 6s 可容纳 SDK 5s grace，不被当成短预算证据。
4. **已修复并独立重验：watchdog fallback 异常未被观察。** 有限 fake 的 startup/close cleanup 各 0.2s，fallback 固定抛 RuntimeError，旧版本 loop handler 收到两条 Task exception was never retrieved。第二候选 terminate_on_deadline 捕获 fallback 错误并记录 cleanup_error 类别，独立 gc/loop-handler 实测无该异常且两阶段健康明确 fault=true。
5. **Main 已修复，待最终候选重验：token-only 取消后排队效果仍执行。** ACP adapter.cancel/close_session 仅 signal token；Foreground checkpoint quiesce 也仅 token.cancel，Core 允许动作结果持久后停止，不能假定所有入口会 Task.cancel。第二候选真实 Root→Controller→Manager→Owner probe 在已批准但 SDK 尚未调用时仅 cancel token，仍 effects=[0,1] 且第二非错误，原始 `supervision-queue-token-red.json`。Main 的 check_current 加入 token.raise_if_cancelled，让 owner 调用前拒绝并保持可持久化错误结果，不打断已开始首调用；独立 main probe 已核 effects=[0]、后续[0,2]、连接与目录保留。第三冻结将再正式绑定重验。

## 初步核验与边界

- 对首候选形成前的源码独立运行 MCP 21 项，15.468s PASS；包含官方 SDK handshake/discovery/call/idle-exit/startup pre-yield deadline、重复取消、跨 Task close。尚不能覆盖最终修复后的完整字节。
- 自建真实官方 SDK server，经实际 Root→Controller→Manager→SDK 验证 invalid 参数审批前拒绝、业务 isError 未包装成功、业务错误后连接保留且后续成功，调用记录仅 [1,2]，关闭后自有 child 消失。`supervision-sdk-bridge-probe.py/json` 为实际脚本与物证。
- 已读 Root schema/风险/双审批/source-check、Plugins runtime、MCP 生命周期及 Feature 契约。发现工具只按本地 risk 表发布，不接受服务器自报低风险；schema 外部引用不允许网络/文件取回，当前定义在审批后及实际队列执行前核验；业务错误、调用前拒绝和真实协议故障必须保持独立。
- SDK 1.29.1 私有生命周期兼容点仅从当前 adapter 持有的 stdio generator 获取本进程句柄/streams，使用 SDK 自带终止，不枚举机器 PID、不编写协议、不由另一个 Task 退出 AnyIO scope；真实 pre-yield 故障测试必要。第二候选上述实际短关闭预算探针确认该机制也覆盖关闭期限。
- 所有探针为离线固定 fixture，无 Provider 调用、任意 shell/路径、用户数据库、配置、原 8787 Host、authentication 三文件或产品文件修改。独立监督只写本目录脚本/证据。
- 非阻断现有语义台账（只读代码观察）：notify/interact 提案进入 UI await 后，源撤销不会在返回完成结果时再次检查 `is_active`。因此 Feature 泛称“旧 proposal 失败闭合”不能解释为撤回已经显示的 UI 或丢弃其返回值。本轮已关闭 typed action 在源/目标审批与实际队列执行前的撤销窗口；UI 完成值没有升级成文件/网络动作授权，不能推断任务验证。主 Agent 已明确保留该限制，不扩围新增 UI 机制；没有把只读观察描述成实际 UI 验收。

正式放行等待第三候选及其关键反例/完整标准外层 summary/终态物证核验。当前没有真实模型、跨平台 CI 或重新手机验收声明。
