# S16 兼容与预算独立监督

结论：**CHANGES_REQUESTED**。本结论绑定候选提交 `4ed1a6a9d7ab315168ced21d14174638fea60e8c`、树 `08d47ac626fc847155c14f628ec2fa41985fd988`；仅覆盖相对 `90e4879a082a13ed3413a5a4337cb5ddace48250` 的 15 个兼容、预算和测试路径，不是 S16 总体验收。

## 阻塞发现

授权将默认工具预算改为 20,000、总提示预算改为 300,000 后，三个原有根测试仍把默认 Host 上限硬编码为 20,000。独立运行候选 Python 3.13 的三个模块，实际 **10 测试、13 子场景失败、退出 1**，见 `compat-review-old-budget-assumptions.log`。

- `tests/test_prepared_context_assembly.py:84`：默认 profile 的 context_window 为 32,000，新 Host 上限实际 32,000；四策略普通路径和四策略 oversized 路径均在旧 20,000 断言处失败。该 oversized 回归本来检验最终 Skill 前缀跨过 20,000 后零 HTTP，不能只把断言调大或改变超限样本。建议将这个旧边界 fixture 显式冻结为 20,000；原请求正文、超限样本、计数和零 HTTP/零 usage 断言保持。
- `tests/test_project_memory_application.py:95`：配置模型窗口 128,000，新 Host 上限实际 128,000，旧默认 20,000 断言失败一次。按配置模型与授权默认收窄后的实际期望检查，同时保留记忆保存、重启、项目隔离和真实离线请求正文断言。
- `tests/test_context_selection_freeze.py:50`：配置模型窗口 200,000，新冻结 Host 事实实际 200,000，四策略旧断言失败。此处是 Host 事实，不是显式 WindowPolicy 的工作窗；应更新默认事实期望，保持 context selection 的不可变、容量漂移、序列化和恢复检查。

## 已独立验证的限定结果

- 逐项阅读同 Task timeout backport、LifecycleOwner/manager 替换、Python `<3.11` 条件依赖及 lock：SDK、AnyIO 版本未变，期限未增加，没有跨 Task 搬移 SDK owner，也没有包围持久 SDK CancelScope 的 AnyIO timeout scope。
- 独立运行 deadline 六例：Python 3.10 与 3.13 均实际退出 0，包含当前 Task 清理、普通外部取消、嵌套内外期限、正常退出撤销定时器、持久 AnyIO scope 与 idle ping。Python 3.10 backport 不具有 3.11 的 cancelling/uncancel API，不能据这些例子宣称同时发生外部取消与期限触发的所有竞态完全等价。
- 独立运行候选 MCP 全 Feature：Python 3.13，33 测试、19.682 秒、退出 0，见 `compat-review-mcp313-final.log`。fault fixture 在 setup 加载父进程 SDK，真实服务子进程仍冷加载，启动期限保持 1.5 秒；这是将依赖准备与故障响应期限分开，不是冷启动证明。
- 因此另读脚本并独立执行新进程冷启动：Python 3.10/3.13 在 start 前 `mcp` 未加载，生产默认期限保持 15/60/6 秒，真实 SDK 初始化、工具调用和关闭均通过；所属子进程退出且 fixture 目录删除。见 `compat-review-cold310.json`、`compat-review-cold313.json` 及对应日志。
- 用户授权预算后的候选 Python 3.10：预算 10 例、真实 Host 3 例均实际退出 0。Host 主例包含 semantic/summary/boundary/persistent × single/team × Git/非 Git 的 16 格，每格 initial/full Schema，消息最小额度与 safety 保留；显式 2,000 的 Git 首轮和非 Git 合法 disclosure 仍拒绝，Provider 调用为零。非 semantic 明确工作窗 65,536 继续收窄，未因 Host 300,000 扩大。
- 原契约 Units 仍写默认工具 2,000 的措辞已在审查后修正；当前工具和总提示默认与实现一致。历史 ultra “突破 20k 静态上限”一句表述过时，可后续按实际显式模式规则同步，不作为产品阻塞。

## 放行边界

以上三个根回归修复并在新冻结候选复验后，才可重评这份审查结论。本报告不宣称当前完整 30 套、跨平台 CI、真实 GLM、编辑器 ACP 或 S16 总体通过；未改产品、未提交推送、未运行真实 API。
