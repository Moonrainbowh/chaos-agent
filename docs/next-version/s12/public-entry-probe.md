# S12 公开入口初查
2026-10-04，用户提供 https://agent.lack.party，预期上游 http://127.0.0.1:8787。
实际curl Schannel启用默认证书验证（未使用-k）：HTTPS返回530，正文error code:1033；仅表明本客户端到错误页的TLS检查成功，不能证明完整代理/源站链路通过。
本机8787监听127.0.0.1，PID66688，HTTP GET / 返回200；未发现cloudflared.exe进程。未停止本机Host、启动Tunnel、更改防火墙或服务。
1033解释参考 https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1033/ ：Cloudflare未找到健康的cloudflared连接。本阶段继续隔离后端验证；真实入口及手机验收未完成。

后续只读检查找到现有配置 C:/Users/Windows11/.cloudflared/chaos-agent-lack-party.yml，ingress确实为 agent.lack.party → http://127.0.0.1:8787，凭据文件路径存在于同目录。仅读取配置中的隧道标识、路径与ingress，未读取凭据正文；未启动隧道。可在后端候选验证后提出具体入口恢复/隔离验收切换方案。

第三候选期间再次启用默认证书检查curl：HTTP530，TLS_VERIFY=0，正文仍error code:1033（public-entry-final-response.html）。隔离验收副本 phone-tunnel.config.yml 使用同一既有Tunnel与凭据引用，只将上游设为8790；cloudflared ingress validate 返回OK，未run/未改变原配置。

本地真实TLS探针第三候选复测通过 tls-probe-final.log；中途一次空会话WSS在POST启动Task前合法关闭，测试未收到事件而失败，日志tls-probe-empty-session-race.log保留。修正夹具顺序为先启动实际Task再读取其缓存事件，未增加等待或改产品；可信证书/地址检查、HTTPS鉴权及真实WSS任务完成均通过。该临时自签fixture CA由客户端显式信任，不能外推公网代理证书链或手机信任。
