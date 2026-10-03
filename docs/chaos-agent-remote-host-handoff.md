# Remote Host 续接交接（2026-10-02）

本次未在仓库、桌面或已提供附件中找到原 `chaos-agent-remote-host-handoff.md`；此文件是根据当前代码创建的补充交接。使用说明以 `docs/remote-host.md` 为准，2026-09-29 的设计/计划记录保留为历史。

## 当前接线

`cli.run → remote_cli.serve_host → create_host_app → RemoteTaskController → Application.foreground_tasks`；任务启动、事件与中断继续委托现有 ForegroundTaskController，不修改 Agent loop。

两个网络入口共用 HTTP/WebSocket Host：默认 `127.0.0.1:8787` 供独立 cloudflared 上游连接；显式 `--lan` 监听 `0.0.0.0:8787` 供 LAN 手机访问，`--bind` 可限定具体接口。运行时没有 Tailscale 地址发现或 VPN 客户端依赖。

依赖声明和 uv.lock 增加 `websockets`。基础 Uvicorn 安装并不保证包含 WebSocket 协议实现；只通过 Starlette TestClient 不能证明实际 TCP Upgrade 可用，必须保留真实 TCP 回归测试。

## 凭据与撤销

- 浏览器 localStorage 保存长期随机 credential；Host 在产品状态目录只存 SHA-256 摘要。StoreLock、fsync 和原子替换保证跨进程写入与失败重试。
- Host 重启不清除设备摘要；启动 Token 在内存中单次消费，重新配对需要当前 Host 新 Token。
- `chaos-agent host revoke-device` 是无 Agent 初始化的本地命令。每次 HTTP/WS 认证读取当前文件，活动 WS 等待中检查撤销；撤销不会取消已启动任务。
- 前端 HTTP 与 WS 都使用当前 origin；网络/503 保留 credential，401/4401 清除；新 task 重置事件游标。

## 明确限制与继续验收

LAN origin 与 HTTPS origin 不共享浏览器凭据；固定 HTTPS 域名可以覆盖 Wi-Fi/移动网络切换，当前未实现跨 origin 迁移。活动任务事件仅保留在进程内最近 256 条；认证重启恢复不代表任务流重启恢复。当前移动网页未提供 manifest/service worker。

必须用真实手机继续验收 LAN/防火墙、HTTPS Tunnel/WSS、同域名网络切换、浏览器后台恢复、Host/Windows 重启免配对及撤销行为。cloudflared 与域名未在本次会话部署，现有 `scripts/remote_host_autostart.ps1` 未改动、未安装任务计划；P2 仍需用明确工作目录和首次配对码可见性做真实登录验收。cloudflared Windows 服务参考 [官方说明](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/as-a-service/windows/)。

## 测试入口

```powershell
python -m unittest discover -s chaos_agent/remote/tests -v
python scripts/run_tests.py --suite-timeout 300
```

Remote 测试全部使用临时设备文件；包含真实撤销 CLI 子进程与 Node VM 执行当前页面脚本的凭据/重连行为验证。Node 不可用时脚本行为测试明确跳过，不把文本检索当作浏览器运行验收。

真实 LAN 接口测试仅在显式设置 `CHAOS_TEST_LAN_ADDRESS` 后运行；本次在 Windows 的 `192.168.1.4` 接口通过同机 HTTP/WS、Host 重建免配对和撤销检查。它不等同手机到电脑的 Wi-Fi 或 Cloudflare HTTPS/WSS 验收。

## 本次交付文件与验证结果

| 文件 | 原因 |
| --- | --- |
| `chaos_agent/remote/pairing.py` | 在已有摘要持久化改动上补跨进程撤销、StoreLock/fsync 与保存失败不消耗 Token |
| `chaos_agent/remote/server.py` | 异步文件认证、活动 WS 撤销/断连清理、配对保存失败可重试 |
| `chaos_agent/remote/static/index.html` | 保留 location 构造 ws/wss；修复网络错误清凭据、任务游标、重连及 hidden 状态 |
| `chaos_agent/remote/AGENTS.md` | 同步真实凭据与连接生命周期契约 |
| `chaos_agent/remote_cli.py`、`chaos_agent/cli.py` | 保留 localhost/LAN 与撤销入口，统一参数校验，撤销无需初始化 Agent |
| `chaos_agent/remote/tests/test_remote.py` | 配对、持久化、重建、跨 Store 撤销、HTTP/WS 与事件恢复；修复默认目录副作用 |
| `chaos_agent/remote/tests/test_remote_cli.py` | 绑定策略、参数拒绝与真实 CLI 子进程撤销 |
| `chaos_agent/remote/tests/test_remote_network.py` | 真实 Uvicorn TCP HTTP/WS、Host 重建及显式 LAN 接口 |
| `chaos_agent/remote/tests/test_pwa.py`、`test_pwa.js` | 执行当前页面脚本验证 ws/wss、已有凭据、网络错误、未授权与任务游标 |
| `pyproject.toml`、`uv.lock` | 补实际 WebSocket 协议运行依赖，未更新其他依赖版本 |
| `docs/remote-host.md`、本文件 | LAN/Tunnel 使用、存储边界、限制和交接证据 |

- `python -m unittest discover -s chaos_agent/remote/tests -v`：设置当前 LAN 接口后 26 项全部通过，无跳过；包含 Node VM 与真实 TCP，用离线任务替身，不调用模型。
- `python scripts/run_tests.py --suite-timeout 300`：退出码 0，27 个 Feature 套件共 2302 项、根集成 537 项；合计 2839 项，29 项环境条件跳过，无失败。该脚本不发现 Remote 目录，Remote 已独立运行。
- `uv lock --check` 与 `git diff --check`：通过；按会话开始时的 tracked 文件哈希确认其他既有源码内容未改变，无 reset/checkout/清理用户未跟踪内容。
- `python -m build --no-isolation` 未成功：当前环境无可运行 build 模块；改用 `uv build` 成功生成 sdist/wheel，并核验 ZIP 完整性、Host 模块、当前页面与 WebSocket 依赖元数据。构建输出位于本机临时目录，不是已安装版本。
- 初次基线运行时，原有未隔离测试写入了默认 `%LOCALAPPDATA%\chaos-agent\remote\devices.json`；当时未备份，无法恢复或确认此前摘要。若曾配对，可能需要一次重新配对。后续 Remote 测试全部使用临时目录。

## 手机流式输出修复（2026-10-02 续接）

真实手机反馈中，每个 text delta 被创建为单独气泡。Node 回归先复现 12 个片段得到 12 个气泡，再修复为连续片段合并到同一回复气泡，工具调用和任务边界分段，重连重放不重复追加。新增基于文本节点的基础 Markdown 呈现：标题、列表、粗体、行内代码和围栏代码块；模型 HTML 保持文本，不执行。长文本可换行，代码块可横向滚动。

本次仅修改 `remote/static/index.html`、`remote/tests/test_pwa.js`、`remote/AGENTS.md` 和本交接记录，未改变 Agent loop。Remote 套件运行 26 项，25 项通过，显式 LAN 接口测试因未设置地址跳过；包含实际页面脚本和 localhost TCP HTTP/WS 验证。没有为这次静态页面修复重复运行全量套件。手机刷新页面并发送一条新任务即可验证；旧页面的碎片输出不代表新渲染结果。
