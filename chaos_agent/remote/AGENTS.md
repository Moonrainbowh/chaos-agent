# Remote Host
通过 Tailscale 上的 HTTP/WebSocket Host 将手机 PWA 接入电脑端 Chaos Agent。

## 边界
- 负责：一次性配对、单设备认证、单活动任务、移动端事件转换和 HTTP/WebSocket 生命周期。
- 负责：复用已组合的 `Application`、`ForegroundTaskController` 和会话存储，不复制 Agent loop。
- 不负责：Provider、Workspace、权限策略、TUI、云端 Relay、用户账户或公网穿透。

## Units
- `PairingStore`: 生成一次性 Token、签发和校验设备凭据 | 进程内认证状态 | Token 单次消费，设备凭据只保存摘要。
- `RemoteTaskController`: 启动、停止和广播单个远程任务 | Agent 任务与有界事件缓冲 | 手机断线不取消任务。
- `RemoteEventAdapter`: 将 `AgentEvent` 转换为有界 `MobileEvent` | 无外部副作用 | 不泄露 reasoning、凭据、上下文或原始异常正文。
- `create_host_app`: 组合 HTTP/WebSocket 路由和静态 PWA | 请求处理/连接生命周期 | 认证先于会话和任务操作。
