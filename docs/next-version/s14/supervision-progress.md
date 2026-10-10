# S14 独立监督进展（尚未阶段验收）

裁决：**IMPLEMENTATION_PROBES_PASS / FINAL_PENDING**。只读产品，独立编写 docs 下隔离探针；不提前批准 S15。

已复核实际 FD/handle 输出身份传播、异常 publication_committed 与 output_identity、batch success/exception 的完整 same_path_state 校验。success receipt 绑定 plan_id，Host 不再观察当前路径认领文件，缺证明默认保持拒绝认领；v22 durable columns 仍只存 device/inode，进程内 metadata 检查未降低。

- `supervision-ownership-window-green.log`：原独立外部同字节替换→token取消反例实际 1 test 通过，返回 partial_conflict，保留外部 after。原 red 文件保留。
- `supervision-workspace-receipts.log`：实际 5 tests / 1.242s / 0 skip/failure/error。含 create/update 发布后单次故障正常回滚、normal/case-only native move 后单次故障正常回滚、move/case-only 同字节新对象与 delete 外部对象保留、同对象同字节新 mtime 冲突。第一次探针持续故障同时破坏恢复验证，已保留独立失败日志并改成只注入首次故障，未放宽产品。
- `supervision-unit-compatibility-tests.log`：48 tests / 10.721s / 2 skip / 0 failure/error，含新 ownership、full metadata、旧 snapshot missing-only 不改两根、v10/v18 migration、旧 NULL 与 paired proof API。两 skip 实为 Windows 缺 symbolic-link privilege，不能表述为 POSIX 不适用。
- `supervision-v22-records.log`：真实 v22 schema 有效 typed journal，含一个 NULL 与一个 paired POST proof，v22→v26 后 API 完整记录相等；迁移前后 foreign_key_check 空，1 test / .735s 实际通过。
- `supervision-cancel-conflict-green.log`：真实 Application/Foreground/Capture/文件/SQLite/Verification 1 test / 3.883s 通过，所有绝对 Temp 根构造前断言，禁止 Provider 工厂，3 个固定离线工具调用不算模型验证。先 README 实际系统 planner evidence 已 verified generation1，然后 module.py edit foreign 同字节替换并取消；durable partial_conflict、workspace_may_have_changed=true，generation2/subject 改变、assessment unverified，旧 assessment finalize 被拒绝，execution cancelled，任务未终态，未调用 Provider。

真实闭环探针最初纯 README 修改触发新 generation 的合法 SYSTEM_PLANNER 文档 attestation，因而新 assessment 仍 verified；这不是旧 evidence 复用。保留该日志并改用需重新验证的代码 module.py 检查失效。嵌套 mappingproxy JSON 展示失败日志也保留；仅修探针输出序列化。

后续已完成：Root 13 tests / 14.292s / 0 skip/failure/error 独立复跑，包含五操作 foreign/正常取消及 malformed receipt。冻结真实 diff 恰17路径，patch 4fb4eaa91ce239aa1c880d75a9936006e7aefb7392c343aeea19b606a5775223、tree dd6b27f3130405ebcdc48658726e42764e108033；raw main/candidate 均与 manifest 一致，Sessions 去 docstring 后可执行 AST 未变化，auth 原 patch 保留。见 supervision-freeze-verification.json。

`supervision-process-crash.log` 实际1 test / 两窗口 / 1.237s：直接持有的子进程在真实 apply 后 proof 前、真实 proof 后 foreign replace 强制终止；重开均 partial_conflict/CONFLICTED，保 after，前者 NULL、后者 proof present；无 Provider。Windows venv redirector 初次 PID 错误日志保留，直接 sys._base_executable 与原锁定 venv dependencies 运行后 barrier PID 和 Popen PID 一致，无 PID 枚举终止。

`supervision-cancel-conflict-public-pause-confirmed.log` 实际1 test / 3.862s 通过：token-only settled task 为 RUNNING，TaskController._record_cancelled_result 明确只记录运行结果、不竞争生命周期。公开 pause 先持久 PAUSED，再因 unresolved CONFLICTED batch 拒绝 checkpoint（SessionStorageError），checkpoint 数不增、batch 保 CONFLICTED/外部文件不变；generation2 unverified、旧 finalize 被拒、execution cancelled。此安全闸符合保留冲突、不伪造安全 checkpoint 的阶段保证，不阻 S14；但 public pause 请求抛错这一限制必须交付，不能称完整调用成功或有安全恢复点。

职责审计区分两种 snapshot/锁的实际职责，保留既有结构；成本仅单次温缓存/不含 Application startup，并不能证明普遍性能，报告边界合理。仍待候选标准全量实际退出、最终数量对账和独立最终监督。尚未批准阶段完成、简化/删表/GC、提交推送或发布。
