# 跨平台集成包改名设计

## 目标

将顶层集成包从 `code_agent_win` 更名为 `chaos_agent`，消除项目已支持 Windows 与 POSIX 双运行时后仍残留的 Windows 专属产品定位。`src/code_agent/` 保持不变，继续作为核心 Feature 包。

## 范围与边界

- 重命名 `code_agent_win/` 目录及其 Python 包名为 `chaos_agent/`。
- 同步 Python 导入、`unittest.mock.patch` 路径、根级转发入口、打包脚本入口、测试、契约文件和项目文档。
- 将集成层契约改为跨平台表述：按宿主能力选择 Windows/PowerShell 或 POSIX/sh；Windows 专属实现继续保留明确的 Windows 名称。
- 保留 `WindowsLocalRuntime`、`windows_storage_paths.py`、`test_windows_*` 等真正表达平台实现的名称。
- 不建立 `code_agent_win` 兼容包；这是完整迁移，旧导入路径不再是公开契约。
- 不修改与本迁移无关的现有未提交工作。

## 方案

选择 `chaos_agent` 而不是 `code_agent`，因为仓库已经存在 `src/code_agent/`，复用该名字会混淆核心 Feature 包和顶层集成包。`chaos_agent` 与发行项目名一致，且不携带操作系统假设。

迁移按以下边界执行：

1. 先移动集成目录，保留目录内模块内容和 Windows 专属实现行为不变。
2. 全仓库替换指向集成包的模块导入和补丁路径。
3. 更新 `pyproject.toml`、根级 `agent_app.py` / `agent_cli.py` 以及相关启动文档。
4. 更新 `chaos_agent/AGENTS.md` 的标题、边界和 Unit 描述，移除“Windows 应用集成”作为总定位，保留 Windows/POSIX 差异说明。
5. 更新 `src/code_agent/runtime/AGENTS.md` 中的首版基线和 “Windows-first” 表述，明确跨平台运行时选择。

## 兼容与风险

这是有意的导入路径破坏性变更：`import code_agent_win`、以旧模块路径为目标的补丁和旧内部脚本引用不再保证可用。发行命令名 `chaos-agent`、`agent` 和 `chaos-agent-acp` 保持不变，只改变其实现模块路径。

主要风险是漏掉字符串形式的导入路径、文档示例或测试 patch 目标。通过全仓库残留扫描、编译检查和集成包导入测试控制风险。

## 验证

- `rg` 扫描确认源码、测试、入口和有效文档中不再出现 `code_agent_win`，允许迁移记录中明确说明旧名的内容除外。
- `python -m compileall -q src chaos_agent tests` 通过。
- 验证 `import chaos_agent`、`chaos_agent.bootstrap`、`chaos_agent.cli` 和 `chaos_agent.acp_cli`。
- 运行与包导入、CLI、启动和运行时选择相关的定向测试。
- 检查 `git diff`，确认没有覆盖迁移范围之外的既有未提交修改。
