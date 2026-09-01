# Peer Messaging
在同一 OS 用户的本机打开实例之间交换有界纯文本消息，并把 peer 输入与用户授权严格隔离。

## 边界
- 负责：注册带 PID 与进程创建时间身份的本机实例，维护稳定 ref、可重复 name、workspace/thread/task、权限、状态与显式心跳。
- 负责：通过共享的用户级 Sessions SQLite 存储发送纯文本，支持 `queued`、`held`、`delivered`、`refused`、`expired` 状态和可恢复的 CAS claim/ack。
- 负责：按稳定 ref 优先、唯一 name 次之解析收件人；同名必须显式消歧，rename 不改变 ref。
- 负责：限制正文、频率、重复发送、待收与待审批队列，并让过期 claim 可重新领取。
- 不负责：把 peer 文本写为 `role=user`、生成 task steering、授予权限、执行斜杠命令或解释正文中的授权话术。
- 不负责：后台 daemon、跨机器传输、云端中继、历史/文件复制、TUI 命令或 Root dispatcher 接线。
- 同机同用户边界由应用的用户级 SQLite 路径与 OS 文件权限提供；peer 正文始终是不可信的 `PEER` origin。

## Units
- `PeerSession`、`PeerMessage`、`PeerClaim`、稳定枚举：表达实例、纯文本、状态与 lease token | 无副作用 | 消息 origin 只能为 `PEER`，正文不解析授权或命令
- `PeerMessagingService`: 绑定一个 PID+创建时间实例，串行提供 register/heartbeat/close/rename/list/send/claim/renew/get/resolve/ack 窄 API | 调用 Peer store | 名字歧义失败闭合，所有容量与时间窗口有界；close 后同一实例不可复活
- `PeerError` 及子类：表达身份、消歧、限流、队列与 CAS 冲突 | 无副作用 | 不泄露消息正文
- `PeerServiceLimits`: 冻结心跳、TTL、claim、去重、速率及两类队列容量 | 无副作用 | 配置只能收紧 4 KiB 安全转义正文硬上限
