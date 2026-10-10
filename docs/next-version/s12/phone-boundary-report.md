# S12 手机权限边界与状态标签局部修复

## 结论与现场边界

本次修复两个可确定复现的产品缺陷：业务 `PermissionError` 被统一映射为 HTTP 401，导致 PWA 清除合法设备凭据；`accepted_partial` 缺少显示映射，终态被标成“可继续”。它们有确定回归物证，但**不能据此认定前一次真实手机发送后退出就是该 PermissionError 路径**。

主 Agent 后续现场事实为：重新配对后成功拒绝 `.env` 写入并接受部分结果；Task 为 `accepted_partial`，approval denied、decision approved，文件不存在，mutation/evidence 为空。诊断观察为 HTTP 鉴权均 true、无 401、WS close 1000；旧退出未复现。这些现场结论由主 Agent 提供，本次没有读取真实设备凭据、用户消息或真实库，也未操作 live Host。

## 只读定位

- `server.py:authenticated` 原先将所有 handler `PermissionError` 报为“device authorization is revoked or invalid”/401，无法区分设备、项目、文件系统和业务拒绝。
- `static/index.html:api` 仅在当前凭据对应 HTTP 401 时 unpair；WS 4401 同理。清除凭据行为对真实设备失效正确，但放大了上游错误分类。
- `static/index.html:statusText` 原字典缺少 `accepted_partial`/`waiting_decision`，回退文字“可继续”。`catalog.py` 明确将持久 Task.status 投影为会话 status，因此 accepted_partial 显示缺陷不需要假设选错 execution_status。
- `phone_fixture.py` 将显式 Temp Workspace 加入/选择显式 Temp ProjectStore；`restricted_factory(root)` 在解析后的 root 不等于该唯一 Temp workspace 时抛 PermissionError。`RemoteApplications.for_root` 对 primary root 直接返回 primary，不调用 factory；只有另一项目根才触发该门。ProjectStore 和 catalog 都以 resolve 后根建立身份。目前没有证据证明现场选中其他根，不能将这个候选路径写成已发生原因。
- 空 tasks/approval_requests 只说明尚未写入任务/审批，不足以区分鉴权预检查失败、项目组合拒绝或其他开始前故障。若未来再次出现，记录 route/status/auth bool 与受控异常类型即可定位；不需要记录消息/凭据/异常正文。

## 实现边界

新增 `DeviceAuthorizationError(PermissionError)`，仅代表当前设备凭据检查失败。PairingStore 响应锁中的重认证和 RemoteRequestControl 的显式认证失败使用该类型；HTTP responding 回调在重核失败时也抛该类型，使 Broker 接收到真实鉴权失败而不是业务失败。HTTP auth precheck 401 与 WS 4401 原逻辑保留，PairingStore StoreLock 仍跨 durable CAS/唤醒持有。

`authenticated` 将专用设备异常映射为 401；其余 PermissionError 返回安全、无原异常正文的 403 `operation permission denied`。403 不清除 PWA 配对。并未将业务拒绝变为成功，也未削弱审批绑定、TTL、owner/state CAS、撤销顺序或预览边界。

状态文字补充 `accepted_partial → 已接受部分`、`waiting_decision → 待决策`，验证标签仍来自实际 TaskResult，没有伪造验证结论。

## 回归与证据

先运行新增真实 ASGI HTTP 回归，旧代码产生 `401 != 403`；PWA VM 旧代码产生 `可继续 != 已接受部分 · 未验证`。修复后 5 项独立 HTTP 边界回归覆盖：合法设备业务拒绝 403/隐藏细节/设备仍有效，撤销设备 precheck 401 且不进入业务，response Gate 在 precheck 后失效仍 401 且不消费，callback 再认证失效仍 401，持锁内业务拒绝仍 403。

PWA 测试覆盖 HTTP403 保留 localStorage 凭据、原401/4401清理、持久 accepted_partial 会话显示，以及 Task status 事件显示“已接受部分 · 未验证”和待决策文字。测试运行真实页面脚本于 Node VM，未声称为真机 DOM/浏览器验收。

- Remote 完整套件：80 tests，27.812s，OK，1 skipped；日志 `phone-boundary-remote.log`。既有跨实例 StoreLock 撤销顺序回归包含在该套件。
- `node chaos_agent/remote/tests/test_pwa.js`：全部行为检查 PASS。
- 原真实 Root/HTTP/Broker/Dispatcher/完整 offline Foreground 集成：5 tests，8.105s，OK，见 `phone-boundary-integration.log`；批准回合 completed/verified，仅 SYSTEM_PLANNER 证据，模型两轮，没有付费 API。

规则链原新增条目后 Remote 6061 token；在本契约内压缩同义措辞和移除重复“UI/草稿见PWA Unit”指引，保留强制要求及全部原 Unit 边界，未移出强制规则。必要新增要求保持显式凭据失效401/4401、业务403保配对、partial/待决策文字且不补验证。最终生产 `rule_probe.py` 八链均通过，Remote 6000、Interfaces 6000、Sessions 5987；额度6000、总上限20000未改，见 `phone-boundary-rules.log`/`rule-chains.json`。

改动清单与 SHA256 为 `phone-boundary-files.json`。只修改主目录 Remote Unit、邻近回归和受影响契约；未同步候选、未改冻结 manifest、未调用付费模型、未访问真实用户库。
