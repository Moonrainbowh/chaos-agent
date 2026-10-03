# S5：当前 Windows SSH 部署方案

2026-10-03 实际核查：当前进程没有管理员提升权限；未发现 sshd 服务、系统 OpenSSH/sshd.exe 或 TCP 22 监听；未发现 adb。WLAN 当前属于 Public 网络，另外有 Mihomo 虚拟接口。Android 已确认，客户端尚未选定，建议 Termux。

## 已批准的实际动作

可审查脚本：[mobile_ssh_setup.ps1](../scripts/mobile_ssh_setup.ps1)。默认只打印核查结果；只有 `-Apply` 才部署。

1. 经 Windows 管理员提升后安装内置 `OpenSSH.Server~~~~0.0.1.0`。
2. 将安装生成的 `OpenSSH-Server-In-TCP` 规则限定为 **WLAN 接口、LocalSubnet 来源、TCP 22**；当前网络类型保持不变。先限制规则再启动服务。
3. 启动 sshd，启动类型设为 Manual；验证服务、监听和规则。若端口已占用、已有 sshd 或系统要求重启，停止并报告。
4. 核查 SSH 登录后的 Python 环境，进入本工作区启动正式 TUI，再进行手机连接验收。保持已有 Windows 账户认证方式；账户密码或密钥不写入项目。

安装/服务操作依据 [Microsoft OpenSSH 官方指南](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse)。2026-10-03 用户已批准该操作，管理员提升成功，脚本已开始安装内置 OpenSSH Server；最终服务、规则和连接结果待核验。日志为 `artifacts-mobile-ssh-deployment-20261003.log`，完成结果写入同名 `.json`。

## 真机仍缺少的条件

需要 Android 手机上的 SSH 客户端和实际连接。当前会话没有手机控制通道，无法代替真实触屏、中文软键盘及旋转操作。客户端候选与验收步骤见 [手机 SSH 使用说明](mobile-ssh.md)。用户安装客户端或接入可控制手机后，继续记录真实结果；桌面自动化不能充当手机物证。

S5 当前只完成部署核查及可审查方案。服务部署、SSH 登录、Android 触控及断线恢复均未验收，整项任务未完成。

独立监督 `mobile_s5_readiness_review` 已通过部署准备复核；同名防火墙规则的协议/端口限定问题已修复，两条路径都明确限定 TCP 22、WLAN 和 LocalSubnet。脚本 AST 检查和默认只读执行通过，未执行 `-Apply`。

上段为部署前复核记录。用户批准后现已执行 `-Apply`，安装完成前不宣称 SSH 可用。

## 最终电脑端核验

安装及部署脚本返回成功，无需重启。再次实际核验 `sshd=Running`、`StartType=Manual`；规则为启用的 Inbound/Allow，限定 TCP 22、LocalSubnet 和 WLAN。读取的物证保存在 `artifacts-mobile-ssh-verification-20261003.json`；完整部署日志与结果在同名 deployment `.log/.json` 文件中。

本机通过 `192.168.1.4:22` 成功完成公钥扫描，ED25519 指纹为 `SHA256:ILkgjurGaev2ufu66lmf6EuVW8iu442fwlF2Yq9gI0M`。`mobile_s5_readiness_review` 独立读取系统并扫描得到相同结果，电脑端部署通过复核；这不证明账户认证登录或 Android 连接通过。

手机客户端连接参数：地址 `192.168.1.4`，端口 `22`，用户名 `Windows11`，在同一 Wi-Fi 下使用已有 Windows 账户认证。主机 IP 可能随网络变化，届时重新核查。手动启动服务意味着下次电脑重启后需要启动 sshd。

Termux 无法打开；Termshell 官网 1.0.0 APK 经实际解析及独立复核确认缺少 INTERNET 权限，撤回推荐。改用已核查联网权限的 ConnectBot 候选，下载入口及验收边界见 [手机使用说明](mobile-ssh.md)。S5 整体仍未完成。

后续真机网络核查：手机截图显示同一 SSID、IPv4 `192.168.1.72`，用户说明 VPN 未开启。电脑 ping 手机 2/2 成功，ARP MAC 与截图一致。改用 ConnectBot 后手机完成 SSH 握手，截图主机指纹与电脑一致；服务日志于 19:26 记录来自手机的 `Failed password for Windows11`。网络连接已通过，账户认证尚未通过。

## 手机公钥配置

用户确认电脑直接进入桌面，并提供手机生成的 Ed25519 公钥，授权配置密钥登录。`Windows11` 属于 Administrators；当前 `sshd_config` 的 `Match Group administrators` 使用 `C:\ProgramData\ssh\administrators_authorized_keys`。该文件原先不存在，现已创建并写入用户公钥，注释为 `android-phone`。

提升执行和独立提升只读核验的物证分别为 `artifacts-mobile-ssh-key-20261003.json`、`artifacts-mobile-ssh-key-verification-20261003.json`：内容与提供公钥一致，指纹 `SHA256:xQ+5g//7C6VM1WUxpeWPpIcQBgQW7m6rQ6UIykmahHY`，所有者 Administrators，禁用继承，只有 SYSTEM 和 Administrators 的 FullControl 权限；`sshd -t` 成功。未修改 Windows 密码或空密码策略。私钥保留在手机；真实密钥登录与 TUI 操作仍待手机验收。

`mobile_s5_readiness_review` 独立监督通过公钥配置，另独立计算公钥指纹与记录一致。当前没有手机公钥认证成功物证，S5 整体未完成。

随后电脑端 OpenSSH 日志于 2026-10-03 20:23:45 记录 `Accepted publickey for Windows11 from 192.168.1.72`，ED25519 指纹与已登记手机公钥一致，真实手机密钥登录已通过。物证保存于 `artifacts-mobile-ssh-phone-login-20261003.json`。下一步为启动 TUI 并验收手机输入、触控、旋转及重连，S5 整体尚未完成。
