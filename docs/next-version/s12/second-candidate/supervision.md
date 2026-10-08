# S12 独立监督

当前结论：首候选后端 **BLOCKED**，真实手机/公网链路 **BLOCKED**。不签 S12 DONE，不进入 S13。主实现可继续最小修复并重新冻结后验收。

独立监督范围来自原计划与 requirements.md：批准绑定原动作/任务/执行工作区/状态与 owner；TTL、持久 CAS、重放与未知结果恢复；实际 Application 中央 Broker 接线；设备撤销锁、Host 重启及事件缺口；冻结权限复用与 HTTPS/WSS。只读审查产品，所有自测使用显式 TemporaryDirectory，真实仓储构造前路径断言；未打开用户库、修改认证改动、操作 live 8787 或提交推送。

首冻结：37 路径，patch SHA256 `3a5174ca325d1160a1f6975c8df58547bf371f03080723f7711a71ea698d5799`。独立 verify_supervision_snapshot.py 已核物理主目录/候选原字节、patch、4 个实际模块来源均指向候选，authentication 原有 diff SHA256 `7cbeb87002763fbe5de0bf33a4fd68193af6d66e5340be75f2a1edeb776befca` 保持。

## 确认阻断：连续取消导致设备授权锁泄漏

位置：chaos_agent/remote/pairing.py 的 PairingStore.authorized_response。锁由 asyncio.to_thread 获取，第一次 CancelledError 后直接 await acquiring；第二次取消将该 Task 标为取消，不能停止已经执行的 worker。原阻塞者随后释放锁，worker 成功取得本地 threading.Lock 和跨进程文件锁，HTTP 请求已结束且没有剩余 release 路径。后续 revoke/pair/respond 都可能阻塞到 StoreLock 的 90 秒超时；锁不会随请求结束自动释放。

独立复现 independent_double_cancel_probe.py：实际 StoreLock 被同临时库的 blocker 占有；observed worker 入口 threading.Event 为确定性屏障；cancel、单事件循环调度屏障、再次 cancel；请求结束后释放 blocker，再等待 worker 实际取得锁。结果 independent-double-cancel-initial.log：local_lock_leaked=true、file_handle_leaked=true、cancelled_request_entered=false。临时文件断言在 PairingStore 构造前，复现末尾手工清理只限该 fixture 的观察锁，并复核临时库可重新取得锁。

这不是推测或通过扩大等待掩盖的问题。已反馈 Root，要求将 worker 获取后的释放归属交给独立且不会被请求重复取消中断的清理机制，并在新冻结候选复跑确定性取消测试。监督不修改产品。

## 首候选已有通过证据

锁定候选 Python 3.13.2、cwd 和 PYTHONPATH 均指向候选。independent_tests.py / independent-initial.log：**53 测试通过，15.648 秒**。包括仓储18、Broker11、Remote决策10、撤销排序2、传输4、真实Application+原Dispatcher+ASGI4，以及独立新增实际HTTP反例4：过期不写入、外国owner不消费后合法回答成功、替换新Broker不能恢复旧waiter、并发重复回答仅一次执行。仓储构造另有独立守卫拒绝缺省路径和非临时路径。

静态审查与上述证据支持：持久账本追加迁移，完整绑定摘要及CAS/当前状态重核；本地键盘同样先消费再放行；新Broker保留可见账本但不复活旧执行者；Remote决策复用 Foreground/S5，未知结果不会由普通继续绕过；冻结操作不能由请求体替换；网页 textContent 展示有限预览，不泄露原参数正文；受限明文调试显式选择，远程TLS配置可验证。仍须以最终标准30套最后外层 summary 与 end-validation 为后端验收依据。

真实TLS fixture物证支持本机可信证书HTTPS鉴权与WSS事件；它使用离线执行器，不能代替公网证书代理链或真实手机。公共入口1033与手机未验来自 Root 的当期入口物证，监督没有操作该入口或 live Host。phone-entry-proposal.md 未获启动批准，保持真机BLOCKED；修复锁问题并完成最终后端验收后仍不得标全阶段完成。

## 第二候选复审：锁已修复，Foreground 接线仍阻断

第二冻结42路径，patch `fe661d44515af706605061e4298238c4f3c612e1947d694cb9a7c2a050fc00a9`。独立锁owner Task受到强引用保留，请求仅发release信号再shield owner。独立实际StoreLock两次取消反例已通过，原53测试加Unit新取消及独立取消为55项，16.940秒通过，independent-final.log（当前仅第二候选中间证据，不能作最终放行）。Verification默认敏感拒绝、显式许可/越界拒绝/strictbool及相关Root回归共9项12.371秒通过 independent-verification-guard.log。

独立完整 phone_fixture.py（默认无serve）仍失败，independent-foreground-fixture.log：批准后 .env固定占位内容写入，随后Task failed/unknown、ScriptModel仅1次，真实Core→TaskScoped→Ledger subject snapshot仍抛SensitivePathError；拒绝侧waiting_decision/unverified、2次离线回合正常。它不是仓储/直接dispatch层测试可覆盖的故障。

根因：runtime_controls._initial_runner将 RuntimeDispatcherFactory.__call__ 产生的 RestrictedDispatcher 交给 engine_for；该包装层没有editor，新engine_for从dispatcher.editor.guard读标志回退False，未读到原TaskScopedDispatcher.editor.guard的Host能力。child_engine也是受限包装层，同样需保持原冻结策略一致。已反馈Root最小显式传参或有明确契约的能力公开；监督不修改产品。独立suite已补真实Application中Hostguard与Core verifier继承一致性的断言，待第三冻结验证。阶段后端继续BLOCKED，手机/公网仍BLOCKED。
