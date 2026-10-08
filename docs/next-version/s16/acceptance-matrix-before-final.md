# 最终验收矩阵

当前已推送候选 `afbd8f3ee7163cb71959a6af0bdfb77b6c082cdd`，tree `c93d1b408938ddc3f368ad2e969d549dea61d58d`；新 CI37578152123 进行中。PASS 只覆盖实际范围；历史失败与当前未完成项保留。

| 项目 | 状态 | 实际证据与限制 |
|---|---|---|
| 五平台全套、构建、安装 | PASS（463d） | CI37570225953五作业success；各30套/3465发现=执行/零失败错误/无未运行，Windows17skip、Linux/macOS199skip。ci-463d-test-summaries.json及原日志；ci-final-independent-supervision正式PASS |
| Windows根模块覆盖 | PASS（463d） | 两版133唯一模块、716唯一发现与执行ID精确对应，真实监督preflight0，无遗漏/重复/infra错误；每模块600秒，不是全根600秒 |
| 源码/安装包来源 | PASS（463d） | final-candidate-463d-manifest.json，1662Git文件逐byte核验；复用534b两wheel全部Python成员等463d源码、全部成员等已安装4ed包，独立reused-wheels-source-supervision。读取失败的463d重建包不采用 |
| Windows两版仓库外安装 | PASS（4ed成员复用及当前CI） | compat-clean-install310/313实际0：新venv、四入口、三资源、历史/结果、pip check及开发基准；当前五CI另完成当前源码构建安装，不假称旧SHA就是新构建SHA |
| 连续调用/规则/预算/终态 | PASS（离线） | S3/S4/S6/S9及当前完整回归；工具20k、总prompt300k，规则6k与API/安全预留保持，不以模型文字代验证 |
| 主子授权/归属/预算/取消 | PASS（离线） | S7/S8及最终63项高风险抽查；真实子任务成功仍未验收 |
| 未决副作用/未知结果恢复 | PASS（离线） | S5/S12及当前恢复反例，未决不重放；实际取消后的pending用量保持未知 |
| 有界历史/记忆/策略迁移 | PASS（离线） | S10/S11及当前回归；首窗稳定虚拟ID/committed0，统计v2，不伪增持久计数 |
| 手机HTTPS/审批/拒绝→部分/重连 | PASS（S12真机） | report与supervision，用户确认无证书警告、恢复可用、中文部分交付状态；ScriptModel不是真实模型证据 |
| MCP故障/关闭/超时恢复 | PASS（离线及CI） | S13与锁定SDK1.29.1/Python310回退/当前五CI，不外推未测试SDK |
| Rewind脏目录/冲突/取消 | PASS（离线） | S14反例，public pause CONFLICTED后PAUSED/checkpointguard异常限制见upgrade-rollback |
| 旧库升级/保存新数据/恢复备份 | PASS（隔离） | upgrade-rollback.json实际0，schema24→26、旧数据/ID、追加/重开、旧版拒新库、另存后恢复旧备份；未迁移用户库 |
| 真实GLM CLI小任务 | PARTIAL（明确范围） | 分析/无需修改unchanged、修改verified+SYSTEM及不可变五测试、文档接受部分仍unverified、敏感写仅审批阻断非操作者拒绝。原连接与修改未验证失败保留；timeout/retry实际默认60/2，旧未支持TOML字段不生效 |
| 真实VSCode ACP | PASS（隔离扩展宿主） | 实际442/127原生OutputChannel更新、修改SYSTEM回执与五测试；基线重建限制保留，未验用户原插件或未保存buffer |
| 真实手动换窗/笔记/历史恢复 | PASS（v2固定项目） | investigation-v2-independent-supervision：真实window source digest、同Task预算连续、notes正文与历史JSON原文精确一致；不证明笔记内容正确、数小时调查或自然容量轮换 |
| 真实child与完整来源调查 | BLOCKED（待复验） | v1预算暂停；v2 child有真实父归属及30k/4绑定，但首预留33407>30k，在HTTP前正确拒绝；父期限取消，无最终来源回答且笔记行号有误。10settled+1pending80703，93040/1752仅下界，不能记整体完成 |
| 额外child-only样例 | AUTHORIZED / 待真实复验 | 主累计1m、子300k、单prompt300k/tools20k。Host持久父预算修复及公共schema/persistent同guard修复独审PASS；v4真实5次调用全settled28514/890，因schema100k拒绝、无child，保留失败。v5完整公共入口HTTP0预检PASS，首预留33337；不等于真实child成功 |
| 最新候选来源与安装 | PASS（afbd局部） | 新1665文件Git源码archive及两wheel逐byte核验；Python310/313独立venv干净安装actual0，新CI及最终来源独审尚待完成 |
| 最终独立监督 | PENDING | 阶段、63项关键抽查、当前来源/五CI均正式独审，v1/v2结论PARTIAL与阻塞保留；尚无最终S16放行 |

个人 authentication、用户数据库与原8787Host保留，未合并、发布或部署。详见 continuation-state.md、release-readiness.md、comparison.md 与 upgrade-rollback.md。
