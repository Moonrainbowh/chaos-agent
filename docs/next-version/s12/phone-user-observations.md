# 手机验收用户观察

2026-10-05，Asia/Shanghai；本次隔离入口约10:25启动，约10:45到期。

- 用户明确确认两次批准均由真实手机操作；只读后台记录两个不同任务均为completed，两个审批请求均approved，合计两次completed mutation；该聚合计数不单独证明每个Task恰好一次。
- 用户反馈手机页面没有证书警告，断网重连后审批卡和任务状态可以恢复。此项是用户观察，未捕获手机侧网络帧。
- 用户反馈重新进入时需要新的token配对；尚未确定发生在Host重开之后，还是本次Host持续运行期间。页面实现将设备凭据保存在同源localStorage；隔离Host重开会使用全新设备存储，旧凭据不能沿用。不能据此排除同一Host期间的配对保持问题。
- 用户对“拒绝→接受部分”反馈“不知道”；本次只读后台尚无denied或decision记录，此项仍待操作验收。

## 10:43 拒绝与接受部分

用户提交手机截图：写入.env已拒绝，Accept partial delivery卡已处理，其余互斥决策卡已失效。隔离诊断实例s12-phone-o7qms11g的只读后台确认task f6663dabc0604a87a0714d9473c5127d为accepted_partial，动作审批denied，接受部分决策approved且decision_transition为accepted_partial；mutation和verification_evidence为空，.env不存在。截图保存为phone-deny-partial.png，后台事实保存为phone-deny-partial-facts.json。

本轮HTTP鉴权为true，创建任务和两次响应均200，WS正常关闭1000；未复现此前发送后退回配对页，不能据此宣称该现象根因已确定。源代码发现普通PermissionError被误分类401会触发PWA unpair，以及accepted_partial缺少中文状态映射误显示“可继续”，进入S12局部修复与重新验证；在此之前不宣布阶段通过。

## 第四候选手机复验

46路径第四候选绑定phone-diagnostic-runtime.json。用户确认发送正常，拒绝后收到固定ScriptModel说明。只读记录f40c6f8a3a7047fe927dd403549982f0为completed，动作denied，无mutation/evidence/file；另只取枚举字段核实该Task intent=analyze、interaction_mode=ask，无verified completion row。非MODIFY任务拒绝动作后可以结束，不能声称该Task已接受部分，也不能把完成视为已验证。phone-fourth-deny-facts.json记录此轮。

此前“发送任意任务”的验收提示未控制任务意图；已改请用户新建会话，发送与隔离self-test相同的明确Implement固定任务，再执行拒绝→接受部分。此对照与新标签真机结果待确认。设备无退出复验已获用户确认，旧退出根因仍不定论。

## 第四候选明确修改任务与收尾

用户随后发送固定Implement任务。phone-fourth-modify-partial-facts.json核Task0388577289174d37b49331cafcf0ce15为accepted_partial，动作审批denied，接受部分决策approved→accepted_partial，其他两决定stale；无mutation/evidence，.env不存在。两截图保存为phone-fourth-modify-deny.png和phone-fourth-modify-partial.png，支持审批/决策状态，但初始旧页面顶栏仍显示“可继续”。

请用户用同源版本参数地址https://agent.lack.party/?s12-ui=2f3a82bb重新载入页面后，用户明确回复“已显示已接受部分 · 未验证”。该标签是用户观察，并非两张旧截图直接证明；确认记录phone-fourth-user-confirmation.json绑定新Task事实hash及第四候选runtime。没有新增产品修复：实跑end_validation再次确认46主/候选字节与2f3a82patch一致，3383完整测试仍对应同一候选。此前DOM可能未重新加载；不能声称旧DOM源码已被手机直接采集。

11:08仅停止本次隔离Host64828及Tunnel73072，按记录创建时间及命令行核身份；phone-fourth-cleanup.json实核这两个实例停止、8790无监听、原127.0.0.1:8787 PID66688保留。未调用真实模型、未修改原配置/用户库/防火墙/服务；临时公网验收入口结束。

S12保持IN_PROGRESS，不据固定ScriptModel回复判定任务或真机验收通过；真实模型未调用。

## 最终候选与证据复用（回归进行中）

用户确认第四手机标签之后，独立Root HTTP发现/status持久决定一致性缺口，随后复查又发现history查询途中恢复任务的身份竞态。第五、第六候选针对这两个确定缺陷修复；上述“没有新增产品修复”仅描述收到手机确认的当时动作，不是最终产品结论。

第六48路径6b50cf8d，手机审批按钮/response、CAS、owner、TTL与加密部署未改变；没有重新启动公网Host或新增真机运行。独立审查明确可沿用第四手机功能证明，新增只读状态恢复/丢POST回应/409/并发隔离由真实Root HTTP snapshots→实际HTML VM及Event屏障测试覆盖。此为范围复用，不宣称第六在手机重新测试。最终完整标准套件和候选终审尚在进行，阶段签收以report.md/status.md的最终记录为准。
