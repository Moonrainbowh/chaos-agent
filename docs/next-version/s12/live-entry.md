# 已授权的临时公网验收入口

2026-10-05（Asia/Shanghai），用户确认临时隔离入口方案后启动。

- 隔离Fixture Host：127.0.0.1:8790，PID66668，候选44路径48cd5cb7；临时workspace位于s12-phone-30kvyrit，状态/数据库/设备配对独立。原127.0.0.1:8787 PID66688仍在线。
- 既有cloudflared隧道：PID40980，读取本次phone-tunnel.config.yml，未修改原配置、凭据、服务或防火墙；注册两条QUIC连接。公网agent.lack.party本次HTTPS200/默认TLS校验0，Python默认信任链/域名检查与WSS真实握手通过，无效设备4401；受保护HTTP无鉴权401。见public-live-probe.json。
- 手机配对码仅在本次工具/聊天展示给预期用户，未写文件；公网探针没有消费配对码或使用设备凭据。真实手机、合法设备WSS和实际触摸/断网体验仍待用户操作确认及后台持久记录。
- Fixture Host最多20分钟自动退出；独立live_cleanup_guard.py按确切PID/创建时间/命令行识别本次Tunnel，Host退出或期限后停止它，保留原Host。用户操作结束后Agent可提前停止本次进程。

live_read_facts.py只以SQLite只读URI查询明确的Temp数据库，输出有界Task/请求状态与mutation计数/固定文件hash，不输出用户消息、Token、Bearer或私钥。S12仍IN_PROGRESS，不能凭恢复公网入口宣布真机通过。

## 10:25 重新开启

用户明确“重新打开”后，确认上一轮已退出且Tunnel清理完成。新隔离Host PID90388、新Tunnel PID91092，workspace为s12-phone-wi9j349m；原8787 PID66688保留。新配对码仅聊天展示。再次验证公网HTTPS200/可信证书及域名、无鉴权HTTP401、真实WSS加密连接/无效设备4401。新Host约10:45（Asia/Shanghai）自动关闭，清理守卫绑定本次PID/创建时间/命令行，未安装服务或修改原配置。

## 10:37 重新生成配对码

用户明确要求重新生成配对码。运行中的fixture没有在线重发接口，保存第二轮只读后台事实及用户真机观察至phone-live-round-two-facts.json后，仅停止本次隔离Host，守卫清理对应Tunnel。重开隔离Host PID80812、Tunnel PID83600，新workspace为s12-phone-kzkkp02d，预计10:57自动结束；配对码仅聊天展示。原8787 Host保留；本次全新设备存储要求重新配对，不能用来判断同一Host的页面凭据保持问题。

## 10:41 诊断与10:48修复后复验

用户确认此前配对成功、发送才退出且没有另处配对。重开仅记录HTTP路由/鉴权bool/状态码、WS关闭码的phone_diagnostic.py，workspace为s12-phone-o7qms11g；10:43拒绝→接受部分成功，记录无401/4401、WS1000。旧异常未重现，未归因。

修复后的第四候选46路径、patch 2f3a82bb914867aa0cc3285cad519b6e2398e75e5234c39c4716109e2faa6b77于10:48启动新phone_diagnostic.py，workspace s12-phone-fdu276e7，启动时逐项核对候选46文件及原fixture hash，物证phone-diagnostic-runtime.json。临时20分钟、绑定进程身份的清理守卫保持；配对码仅聊天展示；原8787继续保留。修复后手机复验待用户确认，完整测试及独立审查尚在运行，不能把第三候选历史PASS移用于第四候选。
