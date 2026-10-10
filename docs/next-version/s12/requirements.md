# S12 需求与边界
S11 第三候选 34 路径859ed1e、标准30套3320run/30skip/零失败错误、独立44与终审PASS后串行启动。
复用中央 ApprovalBroker 与 ForegroundTaskController，为 Remote 提供审批查询/响应及受限等待决定入口；不另建授权引擎或通用shell。批准必须绑定 Host 冻结的 request/task/action摘要/执行workspace/当前状态版本及owner，具备有效期、消费状态与重复请求幂等；跨任务、旧版本、过期、已消费与已撤销设备拒绝，Host重启可查看持久待决但旧批准不得复活执行。S5未知结果使用既有核对接口，普通继续消息不能绕过。
默认本机Host不变；网络明文LAN必须显式受限调试选择，默认远程浏览器使用HTTPS/WSS保护入口。不部署现有live、不改防火墙、不安装服务、不新云Relay、不要求Tailscale。设备撤销不逆转已执行动作；失效后后续审批不得接受，已经取得批准的动作执行语义如实说明。
需求→Feature Units实现验证→Root组合集成；只改相关Interfaces/Sessions/Remote及必要Host绑定和邻近测试。新DB/项目/状态均显式TemporaryDirectory构造前assert，不操作默认用户库。UI显示动作/项目/有效期、批准/拒绝/过期/已处理与重连快照，不输出密钥/推理/上下文。异步断线不丢待决，事件缺口回持久快照核对。
后端/网页自动化和真实手机/证书代理链分开验收；设备或入口不足时仅真机BLOCKED，完成其余本阶段工作后暂停，不能标S12 DONE或进入S13。S3以后仍未授权提交推送，原authentication改动保持。
