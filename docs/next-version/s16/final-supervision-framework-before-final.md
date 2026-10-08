# S16 最终冻结审查框架

状态：**PENDING_REAL_CHILD_ACCEPTANCE**，不宣布整体 PASS。最终候选固定为 `463dc98099e3275a70daf896507b02d4ae387ae9`，tree `13b4117d86504b66942c6ba86ce0f18936d13f41`，本次独立检查 candidate worktree clean。

## 已完成的非模型门

|门|独立结论|范围及物证|
|---|---|---|
|候选与归档来源|PASS_PROVENANCE_ONLY|1662源文件路径/bytes精确等Git；无个人authentication；final-review-provenance-463d.json|
|复用运行/开发wheel|PASS_PROVENANCE_ONLY|两SHA正确、所有member等已安装4ed包、Python源成员等463d；reused-wheels-source-supervision.md；新PermissionError包未采用|
|五矩阵CI|PASS_CI_SCOPE|run37570225953同HEAD；各3465发现=执行、零失败错误；Windows133模块/716unique IDs并集精确；构建/安装success；ci-final-independent-supervision.json|
|高风险离线反例|限定PASS|实际63项unknown recovery/child scope/owner CAS/result/ACP反例全部0；534b→463d仅测试握手变动，因此生产源码同一|
|Windows监督运行器|限定PASS|8路径SHA绑定，9反例及两版实际受Job监督27项0；windows-grouped-final-supervision.json，保留完整旧716错误|
|S3–S15阶段物证索引|来源PASS|13份patch/snapshot SHA精确；历史阶段物证的范围与独立角色边界保留，不称全部历史测试在最终tree重新执行|
|升级/回退|隔离范围复用|upgrade-rollback.json绑定S15 tree c3a3094、安装wheel；schema24→26、追加/重开、旧程序拒新库、另存后恢复旧备份。独立git比较该tree→463d的sessions/workspace全路径diff为空，因此相关代码同一。日志actual exit0；没有upgrade-rollback.exit单独文件，不能引用不存在的物证|

升级探针只针对自有旧消息/task样本，不能泛化为用户真实数据库迁移或任意损坏恢复。未来schema拒绝可能先配置WAL，不能说文件字节零写入；S14 pause/conflicted checkpoint限制保留。S12手机、真实CLI与VSCode独立证据各保留实际范围，敏感CLI policy block不冒充操作者拒绝。

## 最后真实样例的验收条件

最新用户纠正后，审查以 **父累计1,000,000 token / 子累计300,000 token** 为准，不是父100m。单请求总prompt300k、工具提示20k、模型GLM5.3-flash/medium等其余冻结条件按本次fixture和实际wire/contract重新核对。该预算改为下一独立样例，不改原30k错误、24工具暂停、12/40期限取消或pending80703。

收到实际结果后，将核对：

1. 当前候选HEAD与源hash；owned state/插件trust隔离、manifest digest与may_write=false配置；生产入口、模型/effort和实际output/request约束，禁止以prompt声明当真实配置。
2. raw SQLite child_budget中的父owner、task、delegate、instance、child300k与父1m累计预算；父子origin usage唯一ID和预算同步、不跨窗重置、不重复相加。API空投影不能误判raw绑定不存在。
3. 实际HTTP与usage是否全部settled、无pending/partial未知负债；Provider input/output与owner预算相等。旧v2 pending80703仍独立unknown，不能因新样例完整结算而消失。
4. child稳定成功、advisory=true、真实源工具结果、来源path:line与当前/历史约束逐项正确；不将子建议当SYSTEM verification，不将未执行测试标PASS。
5. 父最终状态/交付与新样例定义一致，取消/失败/未完成真实保留；回合/工具/期限或累计预算守门不足不得提高同次阈值、补造回执或付费重试制造通过。

既有v2已独立确认manual window/note/history正文恢复，但child失败和父取消，因此只是PARTIAL_WITH_BLOCKERS。新child-only样例若成功，只补真实子任务门；不能泛化成长时调查、自然窗口耗尽、性能改善、用户原插件或真实用户数据迁移。最终报告须准确表达原规划仍未实际覆盖的长调查范围，并绑定最后冻结的文档/物证。

未修改生产或候选，未做Provider请求、合并、发布、部署或真实用户数据库操作。
