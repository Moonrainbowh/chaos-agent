# 手机远程控制 Chaos Agent

Chaos Agent Host 提供标准 HTTP + WebSocket 服务。手机不需要安装 Tailscale、VPN 或其他网络客户端；根据手机和电脑是否在同一个 Wi-Fi，选择局域网地址或 HTTPS 域名访问。

## 手机上的项目与会话

### 从 SSH 手机入口迁移的能力（2026-10-03）

Web 与 SSH 共用 `%LOCALAPPDATA%/chaos-agent/projects.json`，已保存的目录即使没有聊天历史也会出现在项目列表。点击“添加项目”可输入电脑绝对路径，或逐级浏览文件夹；“管理”支持确认后移除入口，不删除工程或会话历史。带历史的项目移除入口后仍可查看历史，明确再次选择会重新登记。

聊天页顶部提供“项目 / 会话”，底部提供“输入 / 会话 / 到底 / 状态”（当前会话运行时变为停止）。用户消息使用深绿色背景，正文从顶部显示。草稿按会话隔离并保存到当前浏览器，切换项目、返回或刷新后恢复；发送成功才清除对应草稿。已有单活动任务、历史分页及断线重连继续沿用原 Host。

迁移复用 ProjectStore 及目录浏览，不迁移 ANSI 绘制、终端坐标或 SSH 公钥配置。HTTP 项目操作只更新目录入口和最近选择，不改变运行中的任务身份；首次发送任务才按所选项目创建 Application。

验证物证：`artifacts-web-mobile-migration-tests-20261003.log`、`artifacts-web-mobile-full-regression-20261003.log`；真实 Chromium 使用临时会话库和真实 HTTP 接口，检查 390×844、320×568 和 844×390 的导航、草稿刷新、浏览/选择及移除确认，不调用模型。截图 `artifacts-web-mobile-chat-20261003.png`、`artifacts-web-mobile-project-browser-20261003.png` 等是浏览器检查，不能替代 Android 真机验收。

S1 边界、S2 项目后端、S3 手机交互均经独立监督通过。S3 监督指出的认证面板关闭、迟到最近选择及发送期间导航草稿残留问题已修复并增加延迟响应测试。S4 全量回归 29 套件、2969 tests 通过（29 条件跳过）；另 Remote Host 55 tests 通过（1 条件跳过）、真实 JS 行为、Chromium 交互及 wheel 页面包含检查通过。完整汇总见 `artifacts-web-mobile-migration-summary-20261003.json`。

本次已启动 LAN Host，电脑地址 `http://192.168.1.4:8787`，电脑端 GET `/` 返回 200。新增 WLAN/LocalSubnet TCP 8787 防火墙规则被自动审批以 `blocked by policy` 拒绝，规则未创建；手机访问是否通过仍待实际检查。本次为当前进程启动，未安装开机自启。

配对后，“最近”显示共享持久会话库中的会话，“项目”按任务原始工作区归属列出最近项目。进入项目后可以浏览该项目的历史会话或新建会话；搜索支持标题、会话 ID 和用户/助手的历史正文。会话列表与历史消息均可继续加载，不只保留最近几个会话。

选择会话会加载已保存的消息。刷新页面会恢复上次选择；手机页面使用紧凑单栏列表，聊天输入区固定在底部。没有保存项目归属的旧会话显示在“未归类”中，仍可阅读和搜索；项目目录已不可用时也保留历史，禁止从这些会话启动任务。

已暂停或中断的任务可以按原契约恢复。已经结束的任务续聊时，创建继承原消息的新会话和新任务，保留旧任务状态、预算和记录；页面会提示“延续会话”，随后显示新会话。已有任务恢复使用保存的模型配置，新的项目会话继承 Host 当前模型选择。同一 Host 仍只有一个活动执行槽。

## 实际连接协议

Host 自身没有配置 TLS：默认地址是 `http://127.0.0.1:8787`，局域网直连使用 HTTP + WS。通过配置了 TLS 的 HTTPS 反向代理入口访问时，手机到入口使用 HTTPS + WSS，入口到本机 Host 可以继续使用 HTTP + WS。配对 Token 和 Bearer credential 用于鉴权，不会把 HTTP 连接变成 HTTPS。

## 启动电脑端 Host

默认启动只监听电脑本机，适合 Cloudflare Tunnel 等反向代理：

```powershell
chaos-agent host
```

终端会显示一次性配对 Token：

```text
Chaos Agent Host listening on http://127.0.0.1:8787
Pairing token: XXXX-XXXX-XX
```

如需让同一 Wi-Fi 的手机直接访问，明确启用局域网绑定：

```powershell
chaos-agent host --lan --port 8787
```

此模式监听 `0.0.0.0:8787`。手机使用 Windows 电脑的局域网 IPv4 地址打开，例如 `http://192.168.1.10:8787`。必要时在 Windows 防火墙中允许 8787 端口的专用网络访问。`--bind <address>` 仍可用于明确指定某个本地接口，但不再自动查找 Tailscale 地址。

## 局域网模式

1. 手机和电脑连接同一个 Wi-Fi。
2. 在电脑运行 `chaos-agent host --lan`。
3. 手机浏览器打开电脑的 LAN IP 和端口，例如 `http://192.168.1.10:8787`。
4. 第一次输入终端显示的 Token；配对后浏览器保存长期 device credential。

## 远程模式：Cloudflare Tunnel

Cloudflare Tunnel 是独立的 Windows `cloudflared` 进程，Host 不需要 Cloudflare SDK，也不需要知道请求来自哪里。默认 localhost 绑定可以直接作为 Tunnel 上游：

```yaml
# ~/.cloudflared/config.yml
tunnel: <tunnel-id>
credentials-file: C:\Users\<user>\.cloudflared\<tunnel-id>.json

ingress:
  - hostname: chaos.example.com
    service: http://127.0.0.1:8787
  - service: http_status:404
```

Windows 上先按 Cloudflare 文档完成 Tunnel、DNS 和凭据配置，再单独运行：

对于 Cloudflare 已管理的域名，最小本地管理流程如下（`chaos.example.com` 必须替换为自己的域名）：

```powershell
cloudflared tunnel login
cloudflared tunnel create chaos-agent
cloudflared tunnel route dns chaos-agent chaos.example.com
# 将生成的 tunnel UUID 和 JSON 路径填入上述 config.yml
cloudflared tunnel --config "$HOME\.cloudflared\config.yml" ingress validate
```

配置文件与入口规则参见 [Cloudflare 官方配置说明](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/configuration-file/)。

```powershell
chaos-agent host
cloudflared tunnel --config "$HOME\.cloudflared\config.yml" run
```

手机打开 `https://chaos.example.com`。页面会自动使用 `wss://chaos.example.com/...`；通过局域网 HTTP 页面打开时则使用 `ws://192.168.1.10:8787/...`，不需要手动填写 WebSocket 地址。

## Windows 登录后自动启动（可选）

本仓库提供一个只负责注册 Windows 任务计划的脚本，不会由 `chaos-agent host` 自动执行。它为当前 Windows 用户创建两个登录任务：一个启动 localhost-only Host，另一个启动 `cloudflared`。Tunnel 进程会在 Host 尚未就绪时自动重试连接：

```powershell
.\scripts\remote_host_autostart.ps1 `
  -CloudflaredConfig "$HOME\.cloudflared\config.yml"
```

脚本默认从 `PATH` 查找 `chaos-agent.exe` 和 `cloudflared.exe`。如果它们不在 `PATH`，可以显式传入可执行文件路径：

```powershell
.\scripts\remote_host_autostart.ps1 `
  -ChaosAgentExecutable "C:\path\to\chaos-agent.exe" `
  -CloudflaredExecutable "C:\path\to\cloudflared.exe" `
  -CloudflaredConfig "$HOME\.cloudflared\config.yml"
```

卸载这两个任务：

```powershell
.\scripts\remote_host_autostart.ps1 `
  -Remove
```

该脚本不会启用 `--lan`，因此自动启动路径仍保持 localhost-only，适合 Cloudflare Tunnel；需要局域网直连时继续手动运行 `chaos-agent host --lan`，避免把端口意外暴露到不可信网络。

## 设备凭据与撤销

- Token 只用于首次配对，成功后立即失效；Host 重启时会生成新的 Token，但不会清除已配对设备。
- 手机将长期 credential 保存在浏览器本地存储中。
- Windows 只在现有 `%LOCALAPPDATA%\chaos-agent\remote\devices.json` 保存版本号与 SHA-256 摘要，不保存明文 credential。credential 由 32 个随机字节生成；摘要不能直接作为 Bearer 使用。
- 写入复用项目 `StoreLock` 跨进程锁，在同目录写临时文件、flush/fsync 后原子替换；只有保存成功才消耗 Token。Windows 文件权限继承当前用户产品目录的 ACL；这不是 DPAPI 加密文件，不混入 Provider 密钥存储。非 Windows 新目录使用 0700，临时文件使用用户私有权限。
- 认证读取当前文件，因此单独启动的撤销命令对运行中 Host 有效；文件缺失、损坏或版本不支持时拒绝认证。
- Host 重启、Windows 重启、重新打开浏览器或切换 Wi-Fi/移动网络后，未撤销的 credential 仍然有效。
- 如需让当前手机重新配对，在电脑本地运行：

```powershell
chaos-agent host revoke-device
```

撤销后新的 HTTP/WebSocket 认证立即失败；已连接的 WebSocket 在下一次发送前检查，空闲等待中每 250 ms 检查并以 4401 关闭（不包含系统调度延迟）。手机页面会回到配对界面。已开始的 Agent 任务不会因撤销或手机断线自动取消。

Token 保留在 Host 内存中，成功配对后单次消费；重新配对前重启 Host，取得新的 Token。撤销命令不启动 Agent，也不需要 Provider 配置。

### Wi-Fi 与移动网络切换

持续使用同一个 `https://chaos.example.com` 地址时，网络切换不会改变浏览器站点存储，也不会要求重新配对。

`http://192.168.1.10:8787` 与 HTTPS 域名属于不同 origin，浏览器不会共享它们的 localStorage。当前仍是单设备单 credential 模式：首次改用另一个地址需要配对，并会替换旧 credential；更换浏览器、隐私模式或改变 LAN IP 也不能自动继承原存储。建议日常固定使用 HTTPS 域名；LAN URL 用于单独的局域网使用场景。当前未实现跨 origin 凭据迁移。

## 当前版本行为与限制

- Host 和 `cloudflared` 需要保持运行；可按上面的可选脚本配置 Windows 登录后自动启动。
- 手机断开不会停止电脑上的 Agent 任务。
- 同一时间只允许一个活动任务。
- 本文描述手机浏览器 Host 协议；手机 SSH 终端的另一条使用路径见 [手机 SSH 使用说明](mobile-ssh.md)。两条路径分别验收。
- 不要把 `--lan` 端口直接暴露到公网；公网访问使用 HTTPS Tunnel。

## 常见问题

### 手机无法打开局域网 URL

- 确认 Host 使用了 `--lan`，而不是默认 localhost 模式；
- 确认手机和电脑在同一个 Wi-Fi，且使用电脑当前 LAN IPv4 地址；
- 确认 Windows 网络类型和防火墙允许专用网络访问 8787；
- 确认没有被访客网络的设备隔离策略阻断。

### 页面显示“等待重连”

确认电脑端 Host（以及远程模式下的 `cloudflared`）仍在运行。任务不会因为手机页面暂时断开而自动取消；重新连接后页面会根据任务事件序号继续读取。

页面只有收到 HTTP 401 或 WebSocket 4401 才清除 credential；断网、503 或 Tunnel 临时不可用会保留已配对状态。恢复网络或切回前台时自动查询 `/status`。新任务切换时重置事件序号，防止沿用上一任务的游标漏掉输出。

事件重播每次运行仅在 Host 内存中保留最近 256 个事件，当前运行的公开消息快照另有 1 MiB 上限；持久历史从共享会话库读取，不受该事件数量限制。Host 重启后认证与保存历史仍可读取；失去原进程的任务会在启动时标记为中断，用户可选择会话继续，活动事件缓冲不会跨进程恢复。当前提供移动网页，尚无 manifest/service worker；局域网 HTTP 下可用浏览器访问，安装式 PWA 与离线使用未验收。

### Token 失效或页面要求重新配对

Token 只能使用一次。若长期 credential 被撤销、设备文件损坏或浏览器清除了站点存储，才需要重新输入当前 Host 终端显示的新 Token。
