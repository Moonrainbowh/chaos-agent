# 手机远程控制 Chaos Agent

当前版本使用 Tailscale 连接手机和 Windows 电脑，不需要购买云服务器。

## 使用前提

1. 手机和电脑都安装 Tailscale。
2. 两台设备登录同一个 Tailscale 账号或同一个 Tailnet。
3. 电脑端已经能正常运行 Chaos Agent，并配置好模型 profile。

## 启动电脑端 Host

在项目环境中运行：

```powershell
chaos-agent host
```

Host 会尝试自动发现电脑的 Tailscale IPv4 地址，并显示：

```text
Chaos Agent Host listening on http://100.x.x.x:8787
Pairing token: XXXX-XXXX-XX
```

如果没有自动发现 Tailscale 地址，可以显式绑定：

```powershell
chaos-agent host --bind 100.x.x.x --port 8787
```

将终端中显示的 URL 用手机浏览器打开。第一次打开时输入一次性配对 Token，配对成功后浏览器会记住该手机设备。

## 当前版本行为

- 电脑端 Host 必须保持运行；关闭终端后手机无法连接。
- 手机断开不会停止电脑上的 Agent 任务。
- 同一时间只允许一个活动任务。
- Host 重启后会生成新的配对 Token，需要重新配对。
- `SSH` 只用于开发者调试，不是手机端业务协议。
- Host 只应绑定 Tailscale 地址；不要把端口转发到公网。

## 常见问题

### 手机无法打开 URL

- 确认手机和电脑在同一个 Tailscale 网络；
- 确认 Tailscale 显示两台设备均在线；
- 确认 Host 输出的是 Tailscale 地址，不是 `127.0.0.1`；
- 确认 Windows 防火墙允许该端口在 Tailscale 网络访问。

### 页面显示“等待重连”

先确认电脑端 Host 仍在运行。任务不会因为手机页面暂时断开而自动取消；重新连接后页面会根据任务事件序号继续读取。

### Token 失效

Token 只能使用一次。Host 重启、重新生成 Token 或完成配对后，旧 Token 都不能再次使用。
