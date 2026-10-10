# S12 独立监督

当前结论：第四候选原全套/独立重点PASS，但最终收尾新增确认 `/status` 在部分接受后永久返回旧waiting_decision，与持久Task不一致；阶段终审 **BLOCKED待最小一致性修复**。手机新MODIFY拒绝→接受部分及刷新后中文标签已由用户确认。暂不签 S12 DONE，不进入 S13。下文保留各历史冻结结论。

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

## 第三候选独立复验已通过，等待全套终审

44路径，patch `48cd5cb7d43816902e94750dae3bee920471d87586698c26b3a949c4da94ae58`。产品提供readonly verification_allow_sensitive_paths，由原Host guard经Restricted明确转发、engine_for冻结，不公开editor或爬私有包装链。独立61测试通过22.481秒，independent-third.log：覆盖前述全部边界、真实双取消清理、Verification4默认敏感/显式许可/越界拒绝/strictbool、实际Application的Host标志继承，以及新增完整HTTP→Core→Foreground测试，两模型回合、generation1、typed completed、证据仅system_planner。旧两项阻断均在该候选复验消除。

监督另独立运行原phone_fixture.py默认候选自测，通过 independent-foreground-third.log：批准固定.env一次写入、两次ScriptModel回合、completed、verified只对应risk-appropriate-validation的system_planner证据，无实际模型/verifier证明；拒绝无文件、两回合、等待决定后实际HTTP accept_partial，结果unverified。两侧原批准重放409且无改写。Provider HTTP0、网络监听false、usage只是合成夹具计数，不能外推付费模型或真实手机。

verify_supervision_snapshot.py独立核44主/候选raw bytes、patch、原authentication diff和4模块候选来源通过。independent_rule_probe.py实际候选RuleLoader.render八链通过：Root1542、Host4919、Core4422、Interfaces6000、Context5075、Sessions5987、Remote5995、Verification3880；规则额度6000、总提示20000保持。独立物证supervision-snapshot-verified.json、independent-rule-chains.json已物理存在。

当前没有新确认产品阻断；等待第三候选标准30套最末外层CHAOS_TEST_SUMMARY与end-validation/final-test-summary原字节一致之后再签后端PASS。手机/公网仍BLOCKED，后端通过也不能标S12 DONE或启动S13。

## 正式终审：backend PASS / 真机公网 BLOCKED

最终44路径patch仍为 `48cd5cb7d43816902e94750dae3bee920471d87586698c26b3a949c4da94ae58`。独立verify_supervision_snapshot.py在第三标准结束后再次实际执行退出0：全44路径主目录与候选物理字节逐项SHA256等于snapshot；implementation.patch物理摘要一致；原authentication diff仍为7cbeb870…776befca；实际模块来源为候选。supervision-snapshot-verified.json包含正式分层结论。

已独立核all-tests.log最末外层CHAOS_TEST_SUMMARY，解析内容逐项等于物理final-test-summary.json，并与end-validation.json计数/patch/auth/路径数一致：**30 suites，3378 discovered=run，30 skipped，0 failures/errors/expected failures/unexpected successes，unrun=[]，exit0**。Root680、Remote75、Verification79均完整运行；不是此前首3372或第二3377的旧全套结果。旧失败与被否决物证继续保留。

独立61测试22.481秒OK、真实Core Foreground输出两回合/generation1/completed/system_planner、默认候选完整批准与拒绝/HTTP部分接受两组无Provider或监听的JSON、八规则链6k/总20k守门均由终审脚本再次物理读取并断言。确认连续取消不再遗留设备锁；验证观察继承Host冻结敏感能力，默认拒绝和工作区外拒绝仍有效；审批绑定/CAS/重放/owner/TTL/状态漂移、旧Broker不复活及S5等待决定边界无确认未修缺口。可签本候选后端PASS。

本地真实TLS最终物证tls-probe-result.json核对证书/hostname、HTTPS鉴权/未鉴权拒绝及WSS任务事件均true；它使用显式信任的fixture CA与离线Foreground。Root保留空会话先接WSS合法关闭的夹具错误，随后改为先启动Task读取缓存事件，通过tls-probe-final.log；未改产品，不将它视为实际手机或公共证书链通过。最新public-entry-probe.md仍记默认TLS验证启用下公共域名HTTP530/1033，curl TLS_VERIFY=0仅说明错误页TLS验证成功。phone-tunnel ingress配置validate通过但未run，手机未实际配对操作，不能签其PASS。

**授权边界保持：没有升级本机8787、启动Tunnel、修改防火墙/服务、读取默认用户库或提交推送。** 后端可交付供审查；下一步是由主任务给用户具体临时域名入口方案并等待用途批准，以及真实手机动作验收。未完成前S12整体保持BLOCKED，不得标DONE或进入S13。

## 第四候选权限/手机状态局部修复预审

第四冻结46路径patch `2f3a82bb914867aa0cc3285cad519b6e2398e75e5234c39c4716109e2faa6b77`。第三候选3378全套/PASS应查third-candidate历史，不作为本候选已完整回归结论。已独立执行snapshot-only核46主/候选raw、patch、auth7cbeb870…776befca和4候选模块来源一致，supervision-fourth-preflight.json物理存在。

DeviceAuthorizationError仅由当前设备重认证失败明确抛出，保持HTTP鉴权precheck401、响应StoreLock中重认证及CAS前callback失效401；其余业务PermissionError403且不给原异常细节。响应独立owner锁归属、锁跨durable CAS/唤醒、Repository当前绑定/TTL/state/owner校验没有弱化。Pair token失败401与设备请求凭据失效边界保持。PWA仅当当前credential对应HTTP401/WS4401清配对，403继续保配对；accepted_partial映射已接受部分，waiting_decision映射待决策，验证标签仍取实际结果。

独立67测试通过21.957秒，independent-fourth.log；包含原重点61、新HTTP权限边界5，以及监督自写实际Application/真实SQLite card测试：持锁respond业务PermissionError返回403且隐藏细节、pending未消费/原waiter未唤醒/文件未写，随后同一credential GET/status200、原card合法批准200并正常执行。原重复取消锁清理、撤销排序、CAS、重放、真正失效401及完整Foreground仍通过。独立Node运行真实候选PWA脚本测试全部通过 independent-pwa-fourth.log，覆盖403保存credential、401/4401清除、partial/待决策文字与消息状态；这是VM行为测试，不宣称手机浏览器已复验。

独立真实Loader八链render通过1542/4919/4422/6000/5075/5987/6000/3880，规则6000及总20000保持。没有新确认产品阻断；本候选后端最终结论待其标准30套最末outer+end物证，整体S12继续等待修复后真实手机重验。手机既有证据与限制见phone-evidence-preaudit.md：旧退出未复现不等于已证明根因，本轮新手机必须绑定本候选和fixture hash。

## 第四候选正式后端终审PASS，手机仍待指定MODIFY复验

终审独立verify_supervision_snapshot.py已针对第四候选更新并实际执行退出0，物理核最后outer汇总精确等于final-test-summary.json，并与end-validation.json一致：**30 suites，3383 discovered=run，30 skipped，0 failure/error，unrun=[]，exit0；Root680、Remote80、Verification79**。46路径主/候选原字节逐项SHA等于snapshot，patch仍2f3a82bb…faa6b77，原authdiff7cbeb870…776befca保持，候选模块来源正确。它不同于历史第三3378，不依赖嵌套summary。独立67PASS21.957秒、真实完整Foreground两回合/generation1/completed/仅system_planner、PWA VM及八链6k/20k物证已再次读取断言；第四候选后端正式PASS，supervision-snapshot-verified.json已更新。

phone-fixture-fourth-candidate.log的默认候选ASGI两组通过：approve completed+固定.env，deny明确MODIFY后HTTP部分接受/no file/unverified，两回合、重放409/no rewrite、Provider0/listenerfalse。终审核phone-fixture-candidate-binding.json及phone-fixture-files.json逐文件物理hash，绑定第四46路径snapshot/patch、fixture c24b9873…b569245、offline selftest以及phone-diagnostic-runtime.json；runtime明确candidate/2f3a82…/46paths/start2026-10-05T02:48:36.758856UTC/Temp s12-phone-fdu276e7/offline/Provider0。本轮live_serve_executed=true与offline无监听分别记录，不再引用第三旧servefalse绑定。

新手机截图phone-fourth-analyze-deny.png已实际查看：用户发送“你好”，动作审批已拒绝，顶部“已完成·未验证”。安全contract事实记冻结intent analyze、mode ask，phone-fourth-deny-facts记approval denied、task completed、无mutation/evidence/.env。只读源码确认无变更强制waiting_decision针对MODIFY；ANALYZE未运行验证可completed，不升级verified。所以该手机任务没有待决策卡符合契约，不能作为新的产品Bug，也不能充当新候选MODIFY→accepted_partial标签已验的证据。固定ScriptModel声明不作为验证证据。

当前手机结论PENDING：主任务记录新候选发送正常；此前真实批准、旧诊断拒绝/部分接受均单独保留，本轮待用户以明确Implement修改任务完成拒绝→接受部分并确认新中文终态。旧退出没有重现不等于根因证实已解。主任务记录原8787保持及临时诊断预计自动停止约11:08，监督未操作或证明实际停止。尚不能标S12 DONE或进入S13。

## 第四候选新增真实手机MODIFY证据（待收尾）

phone-fourth-modify-partial-facts.json已物理读取：Task0388577289174d37b49331cafcf0ce15为accepted_partial，动作approval db1f2b9898684750a6ee8d38a1d719ad denied/response0，decision ee18c6b0854445acb0fc79d5fdff838c approved/response1/transition accepted_partial，另两互斥decision stale。mutation、per-task mutation与verification_evidence均空，.env不存在。两手机PNG已实际查看，明确Implement任务、写入拒绝、Accept partial已处理、其他卡已失效，支持动作/决策流程；旧header“可继续”截图不被当作新标签证明。

Root直接转达用户在同源?s12-ui=2f3a82bb重新加载后明确回复“已显示已接受部分 · 未验证”。这项来源是用户观察，不是监督自行看到刷新后截图；可结合新候选源映射与VM回归证明修复UI现场已确认。旧脚本缓存为合理解释但未捕获手机cache帧，仍不把此前退配对现象定为同一根因。

此后独立snapshot-only再核全部46主/候选physical bytes通过，诊断试验test_pwa.js已还原冻结hash，无临时export残留；patch2f3a82bb…faa6b77/auth保持。功能验收可支持本阶段范围PASS，真实模型未调用、付费API与生产任务仍不在本fixture证据范围。正式最终签署待主任务保存用户确认并完成本轮临时Host/Tunnel ownPID+birth安全停止和原8787保持物证；早轮live-cleanup-result.json不能代替第四实例收尾记录。

## 收尾新增确定状态一致性缺口：不能以正确header掩盖旧/status

用户确认phone-fourth-user-confirmation.json已物理保存，绑定MODIFY事实摘要和第四runtime；本轮phone-fourth-cleanup.json对Host64828/Tunnel73072与live-processes.json对应birth/身份报告停止、8790关闭及原66688/loopback8787保持。两项收尾物证已读取。第四冻结raw仍通过。

新增decision-display-report.md观察真实HTTP partial后 `/status.status/result.execution_status`仍waiting_decision，而history.task/session为accepted_partial。监督独立候选新进程运行原doc临时HTTP→VM探针（显式Temp，无产品编辑）再次复现：independent-decision-status-fourth.log，1测试3.506秒OK表示该探针成功观测并断言限制，不表示状态一致。输出完整before/after同旧waiting_decision、history accepted_partial。

根因静态明确：RemoteTaskController.status直接返回RemoteRun缓存；RemoteRequestControl完成accept_partial只更新durable Task/Foreground result，未同步该run或新增权威事件。PWA renderState仅active三态使用globalRun.status，非活动标题来自持久selectedSummary，因此当前刷新/sync能显示正确partial且旧waiting_decision不禁发送；这解释了为何手机标签通过，不消除公开API持续错误状态。报告中的“last-run层”没有在HTTP字段或Feature契约明确排除后续决策，不将该缺口重定义为设计通过。

建议最小修复finished run的/status从对应Task/Foreground result精确重核，或成功决策同步同Task/session run与权威task_status事件。必须避免更新不同/已替代run、保持活动owner及单执行槽、不重跑模型；实际HTTP MODIFY拒绝→partial应断言/status与history及TaskResult一致，兼测另一run/导航/poll仍不混状态。已向Root反馈，无产品修改。此真实一致性问题解决并重新冻结验证前，暂缓S12整体PASS/DONE；现有3383和67仍为第四原字节证据，不自动覆盖后续修复。
