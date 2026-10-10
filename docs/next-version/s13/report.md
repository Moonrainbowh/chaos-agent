# S13 扩展边界与 MCP 生命周期

状态：DONE。最终第三15路径 `5337abd1a19853054e2b75075fd02e5e6e1083062ebea90ed9c361b550193675` 已通过实际标准30套/3423发现=执行/30跳过/零失败错误遗漏、end-validation 与正式独立终审PASS。独立范围为MCP27同8raw保留、第三Root59/S1278重跑、7第三固定探针及11规则链。历史失败与中断候选保留，不作最终放行物证；后续进入S14。

第三候选 `5337abd1a19853054e2b75075fd02e5e6e1083062ebea90ed9c361b550193675` 已冻结15路径，正在完整标准验证。第二候选实际标准exit=1，30套/3422发现=执行/30skip/1失败：Root权限规则固定进程请求生成同名副本，新增扩展检查误将其视为插件转换；现保留固定规则之前的扩展目标进行定义核验，实际执行仍用固定程序。第二候选完整失败日志和字节归档 second-candidate，不能作通过证据。

另独立确认 ACP/前台 checkpoint 存在只取消 Token 的真实入口：已批准的 SDK 队列尚未开始时取消，原实现仍产生第二效果。第三候选在 owner 执行前的联合检查中观察 CancellationToken，拒绝尚未开始的动作并保留健康连接，Core仍记录动作结果后停止。红事实保存在 supervision-queue-token-red.json；邻近59项（含取消队列和既有权限规则）通过。MCP Feature8路径与第二候选完全一致。

MCP 启动（传输、初始化、发现）、调用、取消、关闭由同一 owner Task 持有 SDK contexts，设置截止时间和有界队列；故障撤下工具、Schema 和风险映射。configured/ready/last_success/fault/error_type 分开表达，last_success 仅表示协议交换成功。工具风险以本地批准映射为准，未知调用结果不自动重放。

真实 SDK 1.29.1 的传输尚未 yield 时遇到 deadline，会在 process context 退出中等待。适配器保留自己持有的 stdio context，通过该 context 的 SDK process handle 调用 SDK 自带终止函数并关闭其 streams，原 owner 仍负责退出 contexts。此兼容点涉及锁定 SDK 私有实现；升级 SDK 时必须复跑 pre-yield 故障用例。不枚举或终止其他机器进程，不实现新协议。

Host 在审批前校验扩展 Schema；禁止 Schema 外部引用访问网络/文件，保留本地 $defs。Schema、可见性、本地风险、插件目标与 generation 跨审批绑定，并在真正 SDK 调用前再次核对。独立监督复现了第二个动作排队后撤销插件仍执行的窗口（supervision-queue-red.json）；修复把检查传到 owner 执行位置。调用前明确拒绝只跳过该动作，保留健康连接和目录；SDK 业务 isError 不再被 Host 或插件事件标为成功。

Skills 的方法指令定位、任务激活和预算保留；现有 Plugins 只包装已有能力，未扩展事件、角色、模式或市场，也未删除未知用户插件。离线五紧凑工具对照：13 个固定操作翻译正确，59 个紧凑参数错误及 33 个底层错误拒绝；Schema 估算成本减少约 5.2%，不足以据此拆分工具。固定人工纠错不代表真实模型正确率。Skills.enable_many 部分激活行为与实际 TUI 逐个激活/回退区分记录，未扩展本轮修改。

冻结前自测：MCP 27 项故障/代际与队列测试通过；Host 邻近集成 59 项通过。11 条实际规则链均不超过 6000，提示总上限保持 20000。最终结果以 all-tests.log 的最后一条外层 CHAOS_TEST_SUMMARY、end-validation.json 及监督文件为准。离线 fixture 与真实模型验收分开，未调用真实 Provider。

非阻断范围限制：现有 Plugins notify/interact 等待完成期间撤销，完成结果仍沿用原 UI 语义；本轮动作来源校验覆盖 typed effect 与 SDK 队列，不把 UI 返回推断为文件/网络动作授权或任务验证。实际 TUI 的 Skills 激活/回退与公共 enable_many 部分激活行为分别记录，未以离线小样本推断模型收益。Windows 本机故障验证不能替代 Linux/macOS 平台验证。

无数据库迁移、依赖升级、提交、推送、发布或真实 Host 更新。回退仅按 base-tree.txt 恢复本阶段明确 15 路径的旧字节；新增路径则按阶段前状态移除，不覆盖用户其他修改。原 authentication 三文件受保护。

S15 打包审计交接：当前锁定并实际验证 MCP SDK 1.29.1；pyproject.toml 的发布依赖仍为 mcp>=1.0,<2。SDK 私有生命周期兼容点的版本范围必须在仓库外干净安装时核验/收敛，不能把锁定环境的通过外推到所有可安装 SDK 版本。Host 扩展校验直接使用 jsonschema/referencing，当前由 mcp 的锁定依赖保证；精简包时必须保留这一运行路径。
