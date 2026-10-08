# S16 独立发布准备审查（进展裁决）

裁决：BLOCKED，不能标 S16 DONE 或整体发布 PASS。当前不是新产品缺陷的 CHANGES_REQUESTED；缺口是任务卡明确要求的外部验收与最终交付物。本审查只读候选产品/数据库/Host，仅创建本审查文档和自有 Temp 探针。

## 已核与证据分层

- S15 正式监督及 end-validation/分项总数对应 patch 9fa331043d6596c6c1a68c1b9bb1e5458390ce49cfb3b5efff112cc535d47c21、tree c3a3094d4b557926d081a4f2accdf20caebb47a8；30套3440 discovered=run，其中30skip，0fail/error、unrun空。运行数包含skip，不意味着3440实质通过。本审查独立核16候选raw SHA及patch SHA，不重跑full。
- S3-S15可用snapshot/implementation.patch逐hash核验见audit-progress.json。抽读S1/S2历史、S5恢复、S10上下文迁移、S11终审、S12六候选终审、S14恢复/输出归属、S15发行监督。不是逐个复跑每项历史测试；历史阶段raw被后续授权改动覆盖时不能要求当前raw等于其旧快照。当前候选以最终冻结与后续变更范围为准。
- S2五平台success只绑定d4f8fe8/S2候选及3033测试，不替代S3-S15新候选CI。当前workflow五矩阵增加干净wheel/addon步骤，配置存在不算已运行。
- S15Windows3.10.20/3.13.2干净wheel与addon A证据可沿用实际来源范围。当前版本字符串仍1.0.3，既有同版本wheel不能被无hash地称为新正式版本；候选包须给raw SHA、来源tree、日期与未发布身份，版本范围/验收确定后再冻结。
- S12真机证据准确归属第四候选卡批准/拒绝/部分接受和用户同源标签确认；第五/六只读增量通过真实HTTP→实际页面VM及await竞态验证合理沿用其手机协议范围，不能宣传第六或最终S15候选新真机实跑，更不证明真实Provider。
- S14独立Agent完成审查/探针后因用量限制退出，最终预置监督脚本由主Agent执行。这一边界已诚实记录，不能改称独立监督者亲自执行最终命令。本次新的上下文补抽恢复/迁移，不抹去历史角色边界。

## 独立实际反例

候选Python3.13.2（conda-forge），明确候选src+候选sys.path并断言实际app/database模块来源。读fixture确认session_path/product_state/managed-workspaces均显式Temp；ReplyModel固定离线。

PendingActionRecoveryIntegrationTests三项：合法COMPLETED mutation核对不重放、外部字节改动拒猜成功、同路径同内容替换目录拒foreign；EditBatchMigrationTests四项：v18→v26、损坏唯一索引拒绝、不一致size拒绝、POST identity配对约束。实际7 tests/3.445s/OK，无默认库/Host/付费模型操作。

另实际未来schema27自有Temp探针：当前26抛SessionMigrationError，user_version27与sentinel逻辑内容保留，journal_mode变WAL且raw SHA改变。初始化在版本拒绝前设置journal，因此不能写“未来版本失败闭合即数据库字节零写入”。它不证明数据丢失；升级前先备份且不得让旧程序/候选轮流打开唯一原库。

首future探针因过强raw SHA断言及probe使用sqlite connect context-manager未显式close而失败，并在自有Temp清理出现WinError32；改contextlib.closing后复跑exit0、第二自有Temp物理清理确认。首残留自有Temp路径记JSON；自动策略拒绝包含Remove-Item的清理命令，未绕过，未涉及用户数据。

## 发布矩阵与最小恢复条件

| 项目 | 当前可用结论 | 发布门 |
|---|---|---|
| Windows离线标准/反例 | 最终冻结S15全套；本审查7反例与未来schema拒绝实际通过 | 可作为离线证据 |
| runtime/addon干净安装 | S15Windows两Python来源明确 | 新候选五目标CI待实际运行 |
| 固定故障/恢复 | S5/S10/S12/S13/S14已分阶段监督；本审查抽复原恢复与迁移 | 最终矩阵须逐项映射、保失败与skip |
| 真模型/编辑器 | 无本轮真实验收 | 必须先指定授权模型/预算/隔离项目/编辑器并实际执行 |
| 手机 | 历史真机+明确范围增量证据 | 保范围归属；若最终变更涉及协议再补相应真机 |
| 升级回退 | 有schema迁移及安全恢复局部证据 | 完整备份→升级→查询/恢复→回退演练尚待最终产物 |
| 比较报告 | 离线固定轨迹不能得真实成功率/token/延迟结论 | 同任务/模型/预算/环境实际比较，缺样本明确N/A |
| 发行身份 | 当前1.0.3开发候选，未公开发布 | 绑定最终tree/diff/hash；未授权不push/release/deploy |

升级/回退必须保原始日志、SQLite一致性备份（不能只随意copy运行中的main db忽略WAL）、CAS blobs/快照、配置及workspace identity。旧schema26之外的兼容不可猜；回退程序不等于把升级后的唯一DB降schema，原备份/新数据分别保留，任何新数据合并另行验证。未决工具继续unknown，resolve必须可信操作者、版本/owner/workspace验证，禁止自动重放。S14缺POST证明崩溃保输出/CONFLICTED、public pause可已持久PAUSED但checkpoint失败抛SessionStorageError，须列已知限制，不能写作安全checkpoint成功。

新五平台CI与真实模型/编辑器验收到位、完成release-readiness/最终矩阵/比较/升级回退与候选hash后，再按新freeze交同范围终审。本次没有确认未修复错误成功、授权绕过或数据丢失，不把缺证据说成已发现产品故障。

## 累计候选新增物证

独立读取candidate-manifest后实际核累计candidate.patch、source.zip及两个wheel SHA/bytes与Git309差异名单一致。五个恢复/数据库/MCP关键sourcezip文件与c3a309 tree比较，raw断言首失败：zip含CRLF而tree LF，归一换行后五项均相同。不是确认行为改变；仅证明抽查源内容一致，不写sourcezip逐字节等Git blob。累计patch hash24e9c6ef7d10479033f79b4bcda2b9bf24b4671926e497d870558408dda8891f，来源与文件清单准确绑定。

Root整体升级/回退演练已在自有Temp准备，未完成日志前保持待执行。独立读child确认owned根约束、旧source/新安装模块来源assert、旧message/task identity保留、schema24→26及旧程序拒绝/备份恢复合理；外层sqlite connect上下文不close可能导致Windows锁，已反馈Root修正fixture，不记产品故障。

## 增量复审修订：最终LF候选与升级回退已完成

最终sourcezip已用git -c core.autocrlf=false -c core.eol=lf archive重建，本监督实际核SHA8fa7ec19cdb7c30593ad65b078ef8fafce77f38c111b7180db19478e1a773365/3813812bytes；五个关键文件现在逐raw bytes等c3a309 Git blob。旧CRLF发现与首断言失败保留，已修正，不再作为当前包限制；累计309patch/hash不变。仅抽查五blob，不扩大为全部zip逐blob检查。

主Agent第三ownedTemp 1ivtky01演练实际成功，独立审查脚本/child/JSON/末轮真实日志一致：安装正确runtimewheel、pip check、旧S2源码schema24 seed消息/任务、SQLitebackup、新installed wheel schema26保原记录且追加第二条、新进程重开、旧source拒新schema、另存升级后backup、恢复原backup且hash相同、旧source可读原schema24/消息/同task。两backup连接已closing；第三Temp物理不存在。已完成本synthetic message/task升级回退证据，前文“尚待演练”被本修订替代；没有监督者独立重新跑整套升级，无须再装一次或重跑full。

首SyntaxError与第二连接/WAL/清理失败保留为fixture失败；第二自有Temp vdm10jw5策略拒绝清理而保留，不冒称已删。post-upgrade backup在演练回退前确实生成，随后随第三Temp清理，证明该合成演练步骤而非真实用户资产永久归档。真实发布操作仍须使用持久位置保升级后新数据、工作区和CAS资产；此次没有迁移真实用户数据库或证明所有历史snapshot/approval/预算内容的完整兼容。

当前仍BLOCKED：当前候选五矩阵CI、真实模型/编辑器与最终发布/比较材料门未满足。没有新的确认产品缺陷。已向主Agent指出upgrade-rollback说明末段“需完整重跑”应更新，30skip分属不同suite，不能全写成Windows symlink权限不足。
