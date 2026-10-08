# 最终验收矩阵

当前已推送候选 `afbd8f3ee7163cb71959a6af0bdfb77b6c082cdd`，tree `c93d1b408938ddc3f368ad2e969d549dea61d58d`；新 CI37578152123 全部success。PASS 只覆盖实际范围；历史失败与当前未完成项保留。

| 项目 | 状态 | 实际证据与限制 |
|---|---|---|
| 五平台全套、构建、安装 | PASS（afbd） | CI37578152123五作业success；各30套/3482发现=执行/零失败错误/无未运行，Windows17skip、Linux/macOS199skip。ci-afbd-test-summaries.json及原日志；ci-afbd-final-independent-supervision正式PASS |
| Windows根模块覆盖 | PASS（afbd） | 两版136唯一模块、732唯一发现与执行ID精确对应，无遗漏/重复/infra错误；每模块600秒，不是全根600秒 |
| 源码/安装包来源 | PASS（afbd） | final-candidate-manifest.json，1665Git文件逐byte核验；新runtime/development wheel全部source成员等Git，RECORD摘要/大小核对。final-afbd-provenance-supervision正式PASS；历史读取失败包不采用 |
| Windows两版仓库外安装 | PASS（afbd） | new-afbd8f3e-clean-install310/313实际0：新venv、四入口、三资源、历史/结果、pip check及开发基准；五CI也完成新源码构建安装 |
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
| 真实child与完整来源调查 | FAIL（GLM/medium质量） | v7 child只load contract、0read，未来计划即结束，verificationunknown/advisory不能视为objective达成。父读齐四源，但4of5错误判断与test行号偏1。v7实际6HTTP全部settled33577/1292，原源码不变；v6旧pending95586、v2旧80703保留 |
| 额外child-only样例 | 当前FAIL；下一动作待确认 | 1m/300k/单prompt300k/tools20k、5工具、240/900秒生效；公共入口/guard与原body实际审计通过。v7无期限/tokenbug，父wire覆盖四源通过，child wire源覆盖FAIL。准备GLM/high仅HTTP0，不擅自改变用户指定medium |
| 最新候选来源与安装 | PASS（afbd局部） | 新1665文件Git源码archive及两wheel逐byte核验；Python310/313独立venv干净安装actual0，新CI及来源独审均已PASS |
| 最终独立监督 | BLOCKED | CI/来源/安装均独立PASS；实际child来源质量仍未通过，当前不能S16整体放行或标DONE |

个人 authentication、用户数据库与原8787Host保留，未合并、发布或部署。详见 continuation-state.md、release-readiness.md、comparison.md 与 upgrade-rollback.md。
