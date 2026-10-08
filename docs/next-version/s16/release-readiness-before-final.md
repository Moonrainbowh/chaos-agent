# S16 发布准备

状态：IN_PROGRESS，尚未最终放行。S1–S15 已完成阶段验收；S16 仍需当前五矩阵 CI、真实子任务与手动跨窗回执及最终独立监督。

当前已推送候选 `463dc98099e3275a70daf896507b02d4ae387ae9`，tree `13b4117d86504b66942c6ba86ce0f18936d13f41`，分支 `codex/next-version-s2-baseline`。用户已授权提交推送；未合并、创建 Release、部署或迁移用户数据库。三项个人 authentication 改动未加入候选，原本机 Host 8787 保留。

`final-candidate-manifest.json` 绑定当前源码归档：1662 文件均逐字节等于 Git blob。相对 534b 仅一个已独审 PASS 的 WorkBuddy 测试握手修复，产品源码未变化；复用 534b 运行/开发 wheel 的实际 SHA 与所有 Python 成员均已对当前源码核验，原 4ed 两版干净安装物证按完整成员相同复用。463d 新构建包读取 PermissionError，保留且不采用；不宣称这些新构建包已有效。运行包沿用既有 1.0.3 元数据，不表示新版本已发布。

新 CI `37570225953` 五个平台全部success：各30套、3465发现=执行、0fail/error、无未执行套件，Windows17skip、Linux/macOS199skip；全部完成构建和干净安装。原始日志与机器汇总 ci-463d-test-summaries.json 已核对。前一 534b CI 的 Linux3.10/macOS3.13 已完成测试、构建及安装；Linux3.13 发现=执行3465项、199skip、0fail/1error，错误为旧 WorkBuddy 测试 await 已清空任务指针。单测试修复在两版独立回归与稳定反例中通过，已精确提交推送；原 CI 失败保留。Windows 分模块运行器八文件已独审 PASS，发现、执行、重复、未运行、退出码与错误统计交叉核验；分模块600秒期限不能冒称全根600秒总期限。

真实 GLM5.3-flash/medium CLI 分析、修改、无需修改、文档部分接受和受保护写阻断已有实际结果与 usage。普通修改有 SYSTEM verifier 与不可变五测试 PASS；文档部分接受仍未验证，敏感写只证明审批阻断，不能改称操作者拒绝。真实隔离 VSCode 扩展宿主 ACP 的修改有 SYSTEM 回执、原生 OutputChannel 与五测试物证；不推广为用户原插件、未保存 buffer 或一般编辑器验收。S12 手机 HTTPS、审批/拒绝→部分接受、断线恢复保持原真实范围。

独立审查恢复后已完成63项高风险反例、13份阶段patch与候选来源/包成员核验，阶段与运行器 PASS 不替代最终 S16 PASS。新来源调查使用独立 owned persistent/manual new_context 样例，保持模型/medium、工具20k、总prompt300k，原 semantic 证据不改；f24c真实样例已运行：6个实际请求、51707 input/1545 output，SYSTEM在24工具额度门前paused/unknown，children为空，未通过子任务验收；一次requested持久窗口记录待独审，仅外层exit0不等于完成。用户已明确允许另开12回合/40工具的隔离样例，原失败与质量问题仍保留；新样例执行前预检，最终结论待真实证据。首启动因 objective>1024 被契约拒绝且 HTTP0，保留原失败。此前脚本写入未支持的 timeout_seconds/max_retries 字段不生效，零重试说明已纠正；生产真实默认60秒/2重试，新样例须记录实际配置。

逐项证据见 acceptance-matrix.md；比较范围见 comparison.md；升级/备份恢复与已知限制见 upgrade-rollback.md；准确续接状态见 continuation-state.md。所有失败、未知、跳过及安装/入口范围保留，不以测试数、模型文字或本地产物替代发布门。

当前真实阻塞：第二样例已证明手动跨窗、笔记与原始历史恢复；真实child首次预算预留33407高于样例局部30000，正确在HTTP前拒绝，父任务随后期限取消。子归属与冻结30k/4绑定rawDB成立，worker空绑定数组是收集投影，非产品未绑定。第二样例仍有80703预留未结算，实际93040/1752只是已知下界；不得报usage完整或子任务通过。产品门无需因fixture不足而放宽；准备独立只读child复验，追加调用须用户确认。最终S16仍IN_PROGRESS。

追加只读child复验的可审查产物已完成：investigation-v3-preflight.md 与 run_real_investigation_v3.py，owned80f0a96d1ca2，authorizationPENDING。局部60k、首真实prepare33102/HTTP0，保持其它已批准12/40与20k/300k/模型medium/300秒/child4工具90秒，不重复窗口流程。未发Provider，未创建execution marker；需新授权后才执行一次。
