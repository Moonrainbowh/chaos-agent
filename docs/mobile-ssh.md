# Android 手机 SSH 使用 Chaos Agent

手机通过 SSH 连接电脑，在远端运行同一个 `chaos-agent` TUI。布局参照 URI Agent 的 `auto/wide/compact`，实现复用本项目的输入、命令和任务控制器。

## 启动与布局

远端需要已安装 Chaos Agent、已配置模型、可用的 SSH 服务以及交互式终端。手机客户端需支持 SGR 鼠标报告、bracketed paste 和终端尺寸更新。

### 保存项目的手机入口（第二轮）

当前手机公钥已绑定固定启动器。在 ConnectBot 断开旧连接后重新连接 `Windows11@192.168.1.4:22`，自动显示项目列表；选择项目即可进入 TUI，无需输入 cd 或启动命令。已从已有会话来源导入 13 个可用项目。

项目列表支持最近项目、浏览文件夹、输入绝对路径添加，以及移除入口；移除不会删除工程。记录保存在 `%LOCALAPPDATA%/chaos-agent/projects.json`，重新连接后保留。顶部“项目”用于切换项目，“会话”只列当前项目的历史；进入项目默认打开新会话，不自动恢复旧任务。有草稿时切换需明确确认，取消保留草稿；任务运行期间不能直接切换项目。

新界面采用顶部项目/会话栏、消息顶对齐、深绿色用户消息、底部两行状态和四个大按钮。展开输入后显示换行、发送和滚动按钮。手机 SSH 客户端自身的连接栏和键盘图标由客户端提供。

安装包新增 `chaos-mobile`；源码可运行 `python -m chaos_agent.mobile_cli --seed-project <绝对项目路径>`。这台电脑还有固定入口 `C:\Users\Windows11\chaos-mobile.cmd`，可从任意 cwd 启动。`--check` 只列项目，不启动模型。

常规电脑入口是 `chaos-agent`。当前源码工作区也可以运行：

```powershell
cd F:\code-ai-chaos\chaos-16-agent
python -m chaos_agent.bootstrap
```

该源码命令要求 Python 环境能导入 `src/code_agent`，例如已用 `python -m pip install -e .` 安装项目。在手机 SSH 客户端中使用交互式连接；命令形式为 `ssh -t <user>@<host>`，随后在远端进入项目并启动 TUI。

```text
/layout auto
/layout compact
/layout wide
```

默认 `auto` 按真实终端列数判断：不超过 64 列使用紧凑布局，65 列起使用宽屏布局。`:layout` 等价。手动布局在当前 TUI 中保持，重新启动恢复 `auto`。`/compact` 是上下文压缩命令。

## 触控与输入

普通紧凑视口底部提供操作区：

| 操作 | 行为 |
|---|---|
| 输入 / 收起 | 展开或折叠草稿，保留文本 |
| 命令 | 打开命令列表，再点一次关闭；单击可见选项，沿用已有命令语义 |
| 到底 | 回到最新消息 |
| 暂停 / 状态 | 运行中暂停当前任务，空闲时查看状态 |
| 换行 | 在草稿中插入换行 |
| 发送 / 排队 / 引导 | 沿用 Enter；运行中根据当前提交模式处理同一个任务 |
| 上移 / 下移 | 命令列表中移动选项，普通输入时小步滚动转录 |

认证、审批等模态交互使用“取消 / 确认 / 上移 / 下移”，确认由已有交互处理。输入区最多显示三行，短视口优先保留输入和状态；不足 24 列或 7 行时触控栏可能隐藏，可用键盘或调整字体和视口。

Enter 提交，Ctrl+J 换行，运行时 Tab 切换排队/引导。普通输入中首次 Esc 收起已展开的输入区，已收起时再按 Esc 才请求暂停；命令菜单和模态交互优先处理返回或取消。触控“暂停”按钮直接请求暂停。手机软键盘可直接使用“换行”和“发送”按钮。bracketed paste 的多行内容只进入草稿，需另行提交；普通字符流无法可靠区分粘贴与 Enter，真机需核对客户端实际发送方式。

命令按钮临时保存完整草稿，关闭菜单或执行普通命令后恢复；切换到另一个对话后不恢复旧对话草稿。菜单内键入的是命令筛选文本。点击使用最近绘制的坐标，终端尺寸变化后旧坐标会被拒绝，待重绘后继续操作。

## Android 客户端候选

首个验收客户端建议使用 **Termux**。其官方终端视图源码在启用鼠标跟踪时将单击转换为左键报告，适合验证本次触控路径。安装入口与支持环境见 [Termux 官方说明](https://github.com/termux/termux-app#installation)，行为依据见 [TerminalView.java](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalView.java)。具体版本、手机型号和 Android 版本需在真机验收时记录。

用户反馈 Termux 无法打开，随后推荐的 Termshell 1.0.0 已撤回：2026-10-03 实际下载并解析官网 APK，包名 `com.bytedesk.termshell`，缺少 `android.permission.INTERNET`；独立监督复核一致。Manifest 记录为 `artifacts-termshell-manifest-20261003.json`，下载包 SHA256 为 `4FCDB16AD39CA003E05B48246D711BA33D7552FA0580D71C7451ED35E8332E28`。该缺陷使下载包无法正常直接创建 SSH 网络连接；尚未提取手机安装包作逐字节比对。

替代候选为 ConnectBot 1.10.9-oss，来源 [F-Droid 软件页](https://f-droid.org/en/packages/org.connectbot/)，可从 [清华 F-Droid 镜像下载 APK](https://mirrors.tuna.tsinghua.edu.cn/fdroid/repo/org.connectbot_11009000.apk)。已实际下载并解析确认 `org.connectbot` 包声明 INTERNET 和 ACCESS_NETWORK_STATE。还未在用户手机安装，连接及鼠标触控行为仍需真机验收。

## 断开与恢复

当前 TUI 使用前台任务。TUI 正常关闭时会中断任务并保留现有持久恢复记录；SSH 断线后远端进程是否存活取决于宿主终端与会话管理环境，不能据此承诺任务继续运行。重连后使用 `/会话`、`/恢复 <thread-id>`，或 `chaos-agent resume <thread-id>` 检查持久状态。强制断线的实际恢复行为属于真机验收。

## 本次验证记录

S1–S3 已通过各阶段独立监督。Windows 自动化已覆盖布局阈值、草稿、触控、输入解码、模态认证和 ConPTY 尺寸切换。S4 全量回归与打包结果记录于 [阶段计划](mobile-tui-staged-plan-20261003.md)。

**最新进度**：用户已用 ConnectBot 完成真实手机公钥登录、中文对话及换行/发送触控，第一轮仍缺完整旋转、粘贴与断线恢复验收。第二轮 S1–S4 均经独立监督通过；已部署自动项目入口，电脑端真实 SSH 已验证项目菜单、进入 TUI、顶部触控、取消及 PTY 尺寸变化。第二轮 S5 尚待 Android 重新连接与新布局实际操作，详见 [第二轮阶段计划](mobile-experience-v2-plan-20261003.md)。

真机验收依次检查：中文输入 → 多行粘贴不自动发送 → 单击命令和底部按钮 → 软键盘开关和屏幕旋转 → 运行中排队/暂停 → SSH 断开重连与持久状态。每项记录实际结果后，由独立监督 Agent 核对物证。
