# S12 手机新增证据预审（只读，未签阶段完成）

2026-10-05。产品处于局部修复中；本文只核证据可支持的范围，不把此前后端候选PASS自动迁移到即将修改的新候选，不写S12 DONE。

## 已有支持

- phone-live-round-two-facts.json：两个不同Task均completed，两个approval分别绑定对应Task且approved；completed mutation合计2，固定夹具文件存在、摘要记录、设备已配对。用户在phone-user-observations.md明确两次批准都来自真实手机，手机无证书警告、断网重连后卡片/任务状态恢复。这些支持隔离入口中的实际手机批准流程和用户观察下的连接恢复，不是手机网络帧捕获。
- phone-deny-partial.png已实际查看：Write file .env卡显示已拒绝，Accept partial delivery显示已处理，Stop及Continue互斥卡显示已失效；页面有固定离线ScriptModel说明，不把该文本当执行证据。顶部仍显示“可继续·未验证”，与后台终态不一致，是可直接观察到的状态呈现缺口。
- phone-deny-partial-facts.json：对应Task为accepted_partial，approval denied/response0，decision approved/response1/transition accepted_partial，其余decision stale；mutation、verification_evidence为空且.env不存在。与截图一致，支持实际拒绝未写入以及显式部分接受不推断验证通过。
- phone-transport-observations.jsonl：pair成功后受保护HTTP请求的header鉴权valid=true，创建Task及两次respond为200，WSS端ASGI socket记录close1000；没有本轮401或异常关闭记录。它支持本轮受观察请求没有重现“发送后退回配对页”，不能证明此前现象的根因。

## 证据层级与缺口

1. 批准facts只有全库按status汇总mutation_count2。两个approval与两个completed Task对应，但单靠该汇总不能证明每个Task恰好执行一次。预审已指出最初“各有一次”的过度推断；Root随后明确将observations改为aggregate2，live_read_facts增加perTask计数能力，当前拒绝Task无记录。已有自动化一次写/重放证据仍有效，但不可代替此前手机批准逐Task映射。
2. 拒绝facts的real_phone_confirmed_by_user为false；用户提交“手机截图”的来源归属支持截图证据，不必为了结论把该布尔值改成true。最终报告应明确“用户提供手机截图、后台同Task核对”，若此字段本意是尚未直接询问则给出字段解释。
3. HTTP observation为路径脱敏且无timestamp、Task/request标识和scheme，不能单独绑定各请求到具体Task或证明完整公网TLS链。websocket的http_auth_present/valid=false只检查升级请求header，而产品设备认证在首帧进行；它不是socket未认证的证明，close1000也不是手机侧帧完整性证明。公网默认证书验证与WSS链应引用独立入口实测，并区分本机显式CA探针。
4. 用户说重新进入需要新token，未区分隔离Host重启与同Host持续运行；新Temp设备库重启使旧credential失效符合夹具设计，同Host丢配对仍待明确。generic PermissionError误401是可静态/自动化确定的分类缺口；现有本轮日志未重现401，不能把它定为先前用户现象的唯一实际根因。
5. phone-fixture-candidate-binding.json及phone-fixture-files.json中的serve_executed=false是此前离线候选快照；不能作为本轮live未启动的当前状态。phone-entry-proposal.md也是待批准历史方案。最终记录应补本轮用户授权归属、实际启动/停止时间、隔离Temp范围、运行候选hash/夹具hash与公网链证据，不把旧状态字段当当前事实。
6. 本轮固定ScriptModel及system_planner不证明真实Provider能力、模型输出可信或独立verifier身份。当前截图状态映射、普通权限错误分类需最小修复并候选冻结/必要全套回归；最终阶段签署须结合新冻结证据，不在本预审放行。

所有证据来自已有无凭据文件，监督没有访问live Host/默认用户库、启动任何入口或修改产品。

## 最终监督必须保留的分层

待新修复候选冻结后，核实际运行candidate及fixture hash与snapshot一致，重新验证generic PermissionError不会错误unpair、真正无效/撤销credential仍401/注销，以及accepted_partial终态中文状态/卡片/可操作按钮一致。保留原第三候选标准30套3378与后端PASS作为旧快照证据，新候选结果另记录，不能覆写成新bytes已验。

手机多轮分别记：此前两个批准Task及aggregate mutation2；本轮诊断隔离实例拒绝/HTTP部分接受及截图；修复后新隔离实例手机复验。每轮区分Host是否重启、设备库是否新建、token失效边界、用户观察与传输/账本事实。此次未重现退回配对页不等价此前根因已解决。只有修复候选归属、必要回归和修复后真实手机复验齐备，才提交阶段正式终审。
