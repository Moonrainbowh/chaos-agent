# 斜杠命令精简与模式切换设计

## 目标

把 TUI 斜杠命令缩减为日常可用且真实接通的集合，并用 `/模式` 取代无法切换任意 profile 的 `/模型`。

## 命令集合

保留 `/帮助`、`/状态`、`/清屏`、`/退出`、`/新建`、`/会话`、`/恢复`、`/任务`、`/接受`、`/差异`、`/证据`。Picker 另将 `/模式 low`、`/模式 medium`、`/模式 high`、`/模式 ultra` 直接铺为四个一级候选。

移除 `/诊断`、`/追踪`、`/打开`、`/引导`、`/模型`、`/技能`、`/mcp`、`/语言`、`/主题`、`/颜色`、`/字形`、`/暂停`、`/继续`、`/停止`。运行中普通输入继续承担 steering，`Esc` 负责暂停，`/恢复` 统一会话恢复入口。

## `/模式` 行为

- `/状态` 显示当前 mode 和实际模型。
- 一级 Picker 直接显示 `/模式 low|medium|high|ultra`，一次 `Enter` 在 TUI 空闲时切换主运行时。
- 切换成功后，下一条新任务使用对应 profile、模型、prompt policy、reasoning effort 和冻结快照。
- 活动任务运行时拒绝切换；不改变审批或访问权限。
- 命令面板没有裸 `/模式` 或 mode 二级菜单，不要求额外确认、`使用` 或 profile 名。

## 架构

interfaces Feature 新增 `ModeControl`，只暴露只读摘要与空闲切换回调。Windows 集成层持有 `ModeRegistry`、profiles 和 provider runtime manager，按新 `ModeSnapshot` 重建 context、dispatcher、client 与 runner，再原子替换 controller runner。TUI 仅调用 `ModeControl` 并刷新状态展示。

## 帮助与错误

`/帮助` 输出分组、多行中文说明；`/帮助 <command>` 输出单条命令说明。未知 mode、运行中切换和运行时重建失败均以带内错误显示，旧运行时保持有效。

## 验证

数据驱动测试固定精简命令集合；交互测试覆盖 `/模式` 菜单、成功切换、运行中拒绝、真实模型展示及帮助输出；集成测试证明 runner 在切换后被替换且失败时不污染当前 mode。
