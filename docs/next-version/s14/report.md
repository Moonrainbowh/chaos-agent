# S14 交付报告

状态：DONE。第二候选标准完整进程实际 exit0；独立监督者预置的最终验收脚本完成物证核验并输出 PASS。监督 Agent 在等待全套结束时遇到账户用量限制；最终脚本由主 Agent 执行，代码审查及独立探针均由该监督者先前完成。

## 结果与范围

职责审计没有找到可证明安全删除的恢复结构，因此保留现有机制并停止扩张。本阶段先关闭实际输出身份认领 P1：原子发布时保留实际 FD/handle 的完整身份，Workspace 在成功、异常分类与回滚中核对完整状态；Host 只接收关联批次的不可变回执，验证计划、进度和端点后持久现有 v22 device/inode。事后 pathname 观察不再授权文件归属。

实际修改 18 路径，冻结 patch `230a2006edbd7a6273a327ee9fc777b90a144d24bafb9715d1d53613dc7be1b1`，候选 tree `663bb5a555bd0c5bbbdec3d94bd845272b22f7fb`，基线为 S13 正式接受 tree `1612a5e22e4b6a00f962c3e6b4f727b82daa6b7d`。逐文件 raw SHA 与差异见 snapshot.json / implementation.patch。Workspace 12 路径、Host 1、Sessions 仅受影响 docstring 1、Root 邻近测试 4。没有新 schema/table、数据迁移、删库/快照、提交、推送或发布。

## 职责与成本

四类职责唯一主归属：用户撤销、编辑并发/文件身份、崩溃恢复、工作区隔离/归属，共 141 源文件、19,619 有效行，126 测试文件/908 AST test 方法，13 个特定表名；不重复计数，不把 AST 方法数当本次执行数。逐文件 hash/import 边和调用图见 responsibility-audit.md / inventory.json。修复后按同口径重算为142源文件、19,743有效行，128测试文件/923 AST方法，表仍13；新增一个实际输出证明适配与两测试文件。前后物证见 inventory-after.json / inventory-comparison.json；不以删行数代替安全收益。

Checkpoint CAS 保存全量 lineage 内容和用户撤销事实；typed edit snapshot/journal 保存批准动作的精确前镜像与所有权证明；RewindRuntime 只读；mutation/lineage/repository 锁作用域不同。旧快照 missing-only fallback、旧 NULL 身份记录、工作区绑定及冲突闸均保留。未证明安全替代与迁移收益，未实施删减或 GC。

真实 Direct 任务创建的 300/1500 文件温缓存单次样本分别 0.266/0.240 秒，inventory/snapshot 调用和 workspace_snapshots 行均为0；不包含 Application 构造、startup 对账或后台索引预热。显式首次快照 7.859/33.003 秒，重复快照新增 blob 为0但 SQLite 元数据仍增长；确认代码撤销 22.849/87.423 秒。自有 dirty/untracked 基线实际恢复。详见 cost-probe.md/json/log，不外推普遍性能。首轮探针未关闭 SQLite 导致清理失败留存，修正资源关闭后完整复跑且自有 Temp 根已物理移除。

## 已取得的验证

- 原独立反例 red：外部同字节替换被覆盖为 before；修复后 green：PARTIAL_CONFLICT，外部 after 保留。
- Workspace 整 Feature 实跑486/21skip/exit0；随后补一项 legacy 缺证明测试，最终聚焦12/0skip/exit0，最终标准应发现487 Workspace。
- Root 实跑13/0skip/exit0，覆盖5类操作（create/update/move/case-only/delete）在 apply→证明落库前外部替换/删除后重建，以及正常取消回滚；旧/错误计划、重复/越界/可变回执失败闭合，无 pathname 观察。
- 独立 Feature 5项实际故障探针、最终冻结快照51项邻近/迁移/旧快照兼容（2 Windows symlink privilege skip，旧48项作为较早快照日志保留）、v22 NULL/有证明记录真实迁移与 roundtrip。监督日志分别保留。
- 独立真实 Application/Foreground/Capture/文件/SQLite/Verification：已有 verified evidence 后代码编辑+foreign替换+token取消，实际 tool result 持久 partial conflict，generation1→2、subject变化、旧 evidence 不能 finalize，新状态 unverified、TaskResult cancelled。离线固定调用仅控制轨迹，没有真实模型或 Provider 调用。
- 自有进程实际终止：apply后证明落库前；落库后 foreign替换。重开恢复均 partial conflict/CONFLICTED、保留当前文件。只终止直接持有的 fixture process，不操作用户进程。
- 实际规则链在默认6k/总20k预算内，Workspace/Checkpoints及前序代表目录均可加载；规则额度不增加。

初次 Root focused 曾因旧测试没有保存实际 result 而 NameError，保留 host-focused.log；修正后13项通过。独立探针曾持续注入发布错误并同时击中回滚、JSON mappingproxy 输出失败、纯文档得到新 SYSTEM_PLANNER attestation，以及 Windows venv redirector PID 与子 PID 不同；保留相关失败日志，修正探针而不绕过产品验证。

## 兼容、恢复与限制

BatchApplyResult 的旧字段及默认构造保留，新增 plan_id/post_identities 默认为缺证明。旧观察 API 保留但不能认领输出。旧 durable NULL 输出不按相同 bytes 猜回滚；证明持久前崩溃宁可保留输出并阻后写。v22 现有列/状态机不变。

Token-only cancellation 记录取消执行结果，不竞争 pause/stop lifecycle；真实 public pause 在 unresolved CONFLICTED 批次下可先持久 PAUSED，但安全 checkpoint 被拒（SessionStorageError），不能伪称成功创建安全 checkpoint。独立公开暂停探针已 actual exit0 核PAUSED、CONFLICTED、外部文件不变、checkpoint数不增；监督裁决不阻S14安全保证，但完整pause调用未成功。不绕过冲突闸。

保留用户 authentication 三路径和未知资产、原本机8787 Host；不 reset/clean 真工作区。Linux/macOS、真实模型及最终新 CI 不由本地 Windows 离线验证替代。后续 S15 仍须验证干净 wheel 与 SDK 锁定兼容，S16 才做整体发布验收。

## 最终放行

标准30套实际 exit0，3438 discovered=run、30 skipped、零失败错误、无未运行套件。end_validation.py 核验18路径 main/candidate 原始字节、patch 与 authentication 保护；监督者预置 supervision_finalize.py 再核真实基线、原17路径未变、Sessions 可执行 AST、独立日志和本轮末条外层汇总，生成 supervision.md/json PASS。没有新 Agent 复审；没有拼接不同全套的成绩。



## 首次全套失败与修正

第一17路径候选的标准完整实际exit1，3438 discovered=run/30skip/5errors/0failures/无遗漏，独立CHANGES_REQUESTED；raw、patch、日志、实际退出与监督记录完整保留initial-candidate，不当最终通过。Root三项startup recovery helper遗漏actualresult参数，另产生probe未收尾WinError32。修复test_startup_recovery_fast_path的真实回执调用后该模块实际6项/exit0；不恢复事后观察fallback。

MCP第一个queue用例initialize触发原1.5s启动期限，首run owner同步段实测日志1.100s；当前冷SDK import0.311s+initialize0.416s，共0.743s。候选MCP源码/期限未改、无预加载或skip的完整27项诊断actualexit0，不替代最终全套。未取得首run内部profiler，不能断言磁盘/杀软/负载原因，也不宣称队列逻辑修复。诊断见mcp-failure-diagnosis.md/log/json。第二18路径候选标准完整测试实际通过，包括原期限 MCP 27项；保留首次失败，不承诺任意环境首次启动均低于期限。
