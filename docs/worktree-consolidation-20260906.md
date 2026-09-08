# 工作树整合记录（2026-09-06）

以整合前本地 `main`（`a1b3035`）为基线，将仍有效的未提交工作统一到当前项目。原有工作树保留，不推送远端，不启用私人配置中的上下文策略。

## 来源与处理

盘点时共有 15 个工作树，其中 11 个包含未提交改动；所有工作树 HEAD 均已包含在本地 `main` 历史中。快照包含修改、删除及未忽略的新文件，原暂存区不变。

| 来源 | 处理 |
| --- | --- |
| 当前 `chaos-16-agent` | 合入 README 字体说明和字体回退；预览页采用后续 Slate 单主题布局，保留字体与视口改进。 |
| Codex `b03d` | 合入唯一 Slate 主题、输入区与 Markdown 分隔、UTF-8 5120 字节输入限制、分片粘贴处理及测试。 |
| Codex `d14a` | 合入模型/思考深度变更后开启空对话，以及恢复历史对话时恢复冻结运行时设置的修复。 |
| `chaos-16-context-boundary` | 合入显式启用的上下文窗口、History/Notes、累计任务预算、用量显示、实验说明及测试。 |
| `.cw` 的 `356b…`、`7055…`、`a88a…`、`f448…` | 对比历史文件和主线实现：熔断、命令别名、只读验证已有主线实现；保留主线英文命令、真实压缩和保守意图判断，不恢复旧中文占位界面及无持久化的压缩提示。完整原稿留在快照。 |
| 托管目录的 `0306…`、`a404…`、`e270…` | 工作树短路径、仅复制变更、迁移和 CI 依赖修复已有后续主线实现；不覆盖后续拆分与回归测试。完整原稿留在快照。 |
| Codex `f4cb`、`fd5e`，托管 `81e8…`，原 `main-merge` | 干净且提交已在主线，无新增代码需要合入。 |

处理冲突时同时保留了 Slate 动效、当前任务状态投影、对话重置和当前窗口/累计任务两种用量口径；未将旧版整文件覆盖到新版。

集成补充：CI 离线依赖清单补入 `agent-client-protocol` 和新增 `tiktoken`；增加 Slate 状态绘制后 `/new` 清除旧预算显示的交叉回归测试。字体预览图片与 HTML 已按合并后源码重新生成。

## 恢复材料

- 本地备份目录：`F:\code-ai-chaos\worktree-merge-20260905`。
- `inventory.json` 记录全部原工作树、HEAD、文件清单和 Git 快照引用。
- 每个改动工作树有 `files.zip`、`staged.patch`、`unstaged.patch`、`changes.patch`。
- Git 快照分支前缀：`codex/worktree-backup-20260905/`；用于保留原稿，不代表通过测试的交付分支。
- `classified.json` 和 `residuals.json` 保存按主线历史对比的分类依据；按文件内容识别后，再核对当前拆分后的功能实现。

## 验证

- 原 `main`：`python scripts/run_tests.py` 退出码 0，25 个套件、2242 项测试。
- 整合版本：同一全量命令退出码 0，26 个套件、2291 项测试；各套件报告 OK，保留平台相关跳过。
- 新增跨功能回归：`code_agent.interfaces.tests.test_context_budget_display` 3 项通过，包括 Slate 绘制后 `/new` 清除旧预算。
- 安装实际 `tiktoken` 后补测：`context_windows` 22 项，以及持久化/预算暂停/界面集成 7 项，均通过。
- `uv build` 成功生成 wheel 和 sdist；wheel 在独立虚拟环境中安装成功，`uv pip check` 通过，CLI `--version` 返回 `chaos-agent 1.0.3`。
- `git diff --check` 通过，源码与契约无冲突标记；整合期间复核所有原改动文件及暂存/未暂存补丁均未漂移。
- 日志保存在备份目录：`baseline-tests.log`、`merged-tests.log`、`tokenizer-tests.log`、`build.log`、`wheel-install.log`。

本次只验证本地整合和安装，不重新运行说明文件中的付费模型实验，也不把旧实验结果作为本次新测量。
