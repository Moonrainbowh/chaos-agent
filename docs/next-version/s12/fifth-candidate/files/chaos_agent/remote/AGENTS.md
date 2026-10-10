# Remote
手机PWA接电脑：HTTP/WS、LAN/反向代理。

## 边界
- 一次配对/单设备/单活动任务/移动事件/连接；共Application/Foreground/Sessions不复制loop；不负责Provider/Workspace/policy/TUI/云Relay/账户/穿透。
- 历史按Task source workspace归属，未知不隐藏；已知项目组合app/会话恢复契约，终态继承消息新Task保旧终态，刷新/重启持久重建。
- 共SSH固定用户ProjectStore含无历史项目；登记/历史归属分存，移除不删工程/历史；目录仅浏览文件夹，不调模型/切活动Task，守单槽。
- S12共持久审批/待决定，绑task/action/workspace/version/owner/TTL，过期/重放/撤销拒或等；未知走原核对，继续消息非批准。未Task Skill/插件仅本地，审批不扩冻结权限。
- 仅凭据失效401/4401；业务拒绝403保配对；accepted_partial显示已接受部分、waiting_decision待决策，不补验证。
- 默认HTTPS/WSS、代理上游loopback HTTP；明文调试须显式具体私网IPv4，不改防火墙/装服务。撤销不回滚动作，先提交批准仍有效，后响应拒；证书加载≠手机信任链验收。

## Units
- parse_host_options/HostTransport：初始化前核本机/TLS/显式私网。
- PairingStore/authorized_response：进程内一次Token，设备摘要remote/devices.json；StoreLock串行/原子替换/fsync，成功才消费；认证读当前摘要，撤销/损坏/缺失拒。敏感响应持锁重认证至CAS/唤醒，取消仍等取锁worker并释放。
- RemoteCatalog：ProjectStore+共享库最近项目/会话、registered/recent、公开搜索/分页/建会话；添加/浏览/最近选择/移除/checkpoint先认证，不构造Provider。移除保历史，seed守移除，registry串行/thread外；无归属/不可用目录历史只读。
- RemoteApplications：已知项目惰性组合/关一备用app，共库/当前model，恢复契约。
- RemoteTaskController：新建/恢复/继承会话，单槽/有界run事件/公开消息；原子cursor/result快照，结束run核同Task/version持久结果，不跨run/live；WS固定run/断线不取消；epoch变/gap reset回持久快照，旧cursor不串run；丢回应poll核快照。
- RemoteRequestControl：认证查/答中央审批/最小Task决定；Host UUID(非callID)/摘要/预览/choices，绑定/TTL/CAS/consumed_now阻重复执行；restart无Future审批stale、决定核持久状态；未知走S5，accept_partial显式未验证终态非成功证据。
- RemoteEventAdapter：AgentEvent→有界MobileEvent，无副作用，不泄reasoning/凭据/context/异常正文。
- create_host_app/PWA：HTTP/WS/static+可注入ProjectStore；项目/目录/会话/Task先认证，选项目仅登记/最近不替活动app，目录有界浅层。WS发送前/等待核撤销，ws/wss取location，仅401/4401清凭据。窄单栏紧列表，导航/添加浏览移除确认/项目新建/搜索分页/刷新选中；固定输入/到底/状态四按钮，深绿仅user。草稿按会话(无会话按项目)≤64×1024字符，导航保留/发送成功才清该条，未知项目/不可用目录只读。assistant_delta同气泡、tool/Task分段，文本节点Markdown不执行模型HTML。原子cursor重连，滤旧会话/关闭目录迟到结果；gap/epoch reset先持久快照/请求卡再低cursor，显示审批/拒绝/过期/已处理，离线/旧卡不自动批准。
