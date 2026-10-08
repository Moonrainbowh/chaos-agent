# S9 实施与验收报告

状态：DONE。S8 独立 PASS 后串行实施，修复并冻结后标准全量及独立终审 PASS。

最终自测：Windows / locked CPython 3.13.2，在隔离候选运行 `python scripts/run_tests.py`，外层最终汇总 30 套件 / 3218 discovered=run / 30 skipped / 0 failures / 0 errors / unrun=[]，根 tests 649 项通过，进程 exit 0。完整日志 all-tests.log，结构化结果 final-test-summary.json。物理 end_validation.py 核验 31 路径 main/candidate raw hash 与补丁一致，原 authentication binary diff SHA256 `7cbeb87002763fbe5de0bf33a4fd68193af6d66e5340be75f2a1edeb776befca` 保持。未经实测的平台不据此声称通过。

独立新快照对照 52 项（45 继承 + 7 独立新反例）全部通过，另 18 项守门回归及真实规则/ContextAssembly 构建通过。独立监督 `/root/s8_supervision` 已复跑物理核验并检查外层汇总，在 supervision-final.md 正式 PASS，可进入 S10。

修订记录：首个冻结快照 `211a0acd...` 全量 30 套件 / 3205 项 / 30 skipped / 4 failures / 0 errors，根 tests 646 项中 4 处失败，无未运行套件。原日志、快照与 patch 分别留在 all-tests-initial-failure.log、snapshot-initial.json、implementation-initial.patch。独立旧快照 36 项为 35 PASS / 1 FAIL，否定全仓范围错误令无关读取续租两次；此版本不可放行。

已确认修复方向：明确否定的全仓、目录与深度调查提法不能成为进展范围或初始租约的正向依据；排除目录覆盖父目录及搜索结果链。两处修复完成测试缺少其 unittest 动作需要的项目声明，补齐 fixture 并增加有效最终证明断言后 17 项通过，原完成与修复次数检查保持。连续编辑映射轨迹需走公开 Task API 以获得真实代际；跨窗轨迹则核对真实合同/历史读取候选，不能恢复按工具名或正文计进展。最终快照、全量和独立监督均已通过，结论见本文首段。

当前实际行为：Core 从持久配对动作及 Host 状态重建进展。正文、别名、空换窗、计时或新错误不能清零停滞；有限新读取可以调查，只有冻结目标相关读取、真实内容代际、可信成功验证或实际解决失败可续租。固定接受集合防止 A/B 轮换制造新事实；原硬预算和最多三次续租保持。

Host 首次创建 TaskBudget 补齐真实软租约选择，避免默认初始化直接落到硬上限。风险验证默认启用：文档/低风险使用 Host 许可的轻量证明；较高风险的完成证明绑定最终计划的测试身份，关键风险需测试及构建。显式 `CHAOS_STRUCTURED_VERIFICATION=0` 保留兼容且交付 unverified。实际执行成功与需求验证分开。

无需修改的 MODIFY 任务等待用户决定；明确接受后保留 accepted_partial / unchanged / unverified 的持久 TaskResult，不能被模型解释自动变成完成。Foreground 仍先等待真实执行收尾及 checkpoint，再记录接受结果。

局部证据：监督修复后 Core 最终 202 项、Verification 最终 75 项、相关集成 39 项通过；真实默认装配的原 5 条连续读取/无关读取/重复读取/连续编辑/失败读取轨迹加 3 条否定范围轨迹，以及 Compact/Persistent 两模块，共 15 项通过。Host 23 项测试（含首次软租约修复后）通过；补项目 fixture 的两根模块另 17 项通过。以上计数包含既有测试及交叉重合，不能相加当作总新增数。

`default-verification-before.json` 与 `default-verification-after.json` 使用相同确定性 Provider 和真实 Application/SQLite/工具装配：文档修改从 S8 unverified 到 S9 verified，仅形成 SYSTEM_PLANNER 风险许可证明。未运行真实模型、外部设备或上线服务。

最终规则真实加载及上下文构建：Core 4175、Host 5917、Interfaces 5982 token 全通过 6000 额度，总提示上限 20000 不变。新错误反例另在 Verification/进展报告记录：撤销已有 PASS 不能因摘要变化被当作新进展；最终完成证据仍必须按当前有效证明判断。

限定差异由 snapshot.py 重新冻结为 31 路径 implementation.patch / snapshot.json，SHA256 `c5393147d107863aadaed2eae0464224b914cd1cf8452685ca9d53cd2fafddad`。新增可信合同披露/真实 Notes 与 History 非空读取的有限去重候选，仅帮助初始额度内调查，不作为续租或完成证明。旧 Persistent 原 8 步及内容断言保持。保护原 authentication 改动，不升级真实 Host，不提交/推送、合并或发布。最终全量及独立监督 PASS 已补齐，S10 已放行。

独立审查 fixture 事故：一次仓储重开探针误取默认库 `C:/Users/Windows11/AppData/Local/chaos-agent/sessions.sqlite3`，执行 Repository 初始化与 load_task 查询后因 Task 不在此库失败。该库原已存在；没有调用默认库的 task/message/event 写方法，但缺乏事前 schema/hash，不能声称初始化没有元数据写或迁移。连接及进程已退出，未清理/回滚用户库。第二次修正误猜 wrapper 的 session_path 属性，取路径前即失败，未触库。最终根据 fixture 的实际 patch 作用域解析模块函数，在构造仓储前确认路径与临时 workspace 同源，单独重开反例通过，最终完整 52 项通过。原错误日志与具体操作证据留在 supervision-fixture-incident.md。不能据最终 PASS 抹去首次默认库访问。

详细实现及过程失败见 progress-implementation.md、verification-audit.md，保留旧日志并明确最终结果，未通过的旧运行不能作为本轮通过证据。回退仅恢复冻结 S9 路径到 S8 基线，不能回退 S3/S6/S7/S8 已验收契约。
