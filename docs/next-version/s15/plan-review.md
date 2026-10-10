# S15 独立方案审查

状态：PASS_FOR_MINIMAL_IMPLEMENTATION。此为实施范围审查，不是 S15 最终放行；最终需冻结 diff、仓库外干净安装及实际评估入口证据。

已独立读取原规划 S15/S16 卡、main 与 candidate 的 setup.py/pyproject.toml、Root/Python/Host/Evaluation 契约、CI、CLI/注册表与评估适配器调用链。以下结论来自实际源码搜索，未把“没有样例”当作不存在依赖。

## 可安全精简的最小范围

1. 运行 wheel 排除 `code_agent.evaluation` 全包及 `chaos_agent/continuity_*.py`、`chaos_agent/context_experiment_host.py`。搜索 `chaos_agent/src/scripts/tests/.github` 后，外部引用集中在评估模块本身、scripts 和相关测试，没有发现生产 app/CLI/ACP/mobile 的反向引用。Evaluation 契约明确“不参与生产任务执行”。当前 `find_namespace_packages(include=code_agent*)` 与显式 `chaos_agent` 会带入这些模块，仅排除 tests 不足。必须实际检查 wheel ZIP，不能仅看配置。
2. 最小实现优先保留原源码 import 路径与脚本布局，用明确开发分发/benchmark 入口隔离运行 wheel，避免大规模搬迁造成历史冻结路径失配。若移动源码，应先给迁移映射并更新全部 import、worker `-m` 路径、ROOT/PYTHONPATH 与 freeze 清单；不能把空兼容模块仍放入运行 wheel 后声称评估已去除。
3. 保留固定 40 场景、continuity v1/v2、context long 集及隐藏 oracle、grader、可信观测，不改变评分口径。开发分发可以是带清单的源码包/独立 benchmark artifact；必须能在仓库外重建运行，不依赖 cwd 正好是仓库。
4. TUI 注册表已有 DEFAULT/ADVANCED/INTERNAL 分层：四档 mode 动作已 INTERNAL，`model/effort/permission` 独立。无需重写运行配置或为凑数量删除控制轴。CLI `_HELP` 仍将四档 `--mode` 无说明地放在普通 global options，README 同时介绍四档与任务 mode；可只改帮助分组和文档，将四档明确标注兼容入口，并保留解析、环境变量 `CHAOS_MODE[_*_PROFILE]` 与 frozen ModeSnapshot。
5. 默认 capability strategy 已 HYBRID，context policy 可由 model_metadata 得到或为 None，未发现默认交互强制让用户选择它们。README 的入门配置仍展示 capability_strategy，宜移到单一高级配置说明并保留旧设置支持/现有测试。无需改变默认值。
6. README 收敛到安装、最小配置、一次任务、恢复与实际权限边界；技术细节集中维护并留链接。不要删掉保护路径、unrestricted 和 allow_sensitive_paths 的实际边界。
7. 跟踪日志存在 context-ab-20260909、context-validation-* 和 docs/ui-preview/checks；这些是历史证据，不是运行依赖。先按清单归档/保留并列体积，再决定迁出。仅添加 ignore 不代表完成清理。未知 worktree、.kilo、untracked、用户 DB/配置与 authentication 三路径全保留。
8. Peers/专家/semantic-map/站点适配只冻结新增，本阶段不删除已注册能力；共享 RepoIndex 不动。

## 必须验证的干净发行路径

- 从冻结候选构建 wheel，记录 SHA256、压缩字节、展开字节与文件清单，与同环境基线 wheel 比较；不得把 dependencies 的体积变化混成产品 wheel 降幅。
- wheel 不含 evaluation、continuity_*、context_experiment_host、tests、开发 fixture/日志；运行模块和静态资源应齐全，特别 `chaos_agent.remote/static/index.html` 及其引用 JS/CSS/PWA 资产、authentication catalog_seed.json、providers URI_AGENT_LICENSE.txt。
- 新建本阶段专属临时 venv，从 wheel 安装真实依赖并 `pip check`；清空 PYTHONPATH，cwd 设在仓库外，不用 editable、不启 system-site-packages。核验导入 __file__ 在新 venv 内。必要 entry points 为 chaos-agent、agent、chaos-mobile、chaos-agent-acp；help/version 不需 Provider/Shell 就绪。
- 仓库外跑 CLI history/task list/result 的隔离 owned state fixture，证明只读入口仍可达；不要访问已有用户历史。验证 remote 资源解析及必要应用导入，但不启动/替换原 Host 8787，也不付费模型调用。
- 开发评估 artifact 在仓库外跑 evaluation suite 与固定自检/至少一个真实离线 continuity Host arm；确认 worker 子进程也可 import 开发模块。context-long API 入口只检查 help/import/路径，不调用 API；保留其未实跑边界。
- 新 wheel 不能无证据放宽 MCP 依赖：当前约束 mcp>=1,<2，而 S13 私有兼容只证明锁定 SDK 1.29.1（AnyIO 4.14.2）。应锁定已验证 SDK 或明确另行兼容测试。Host extension_actions 直接使用 jsonschema/referencing，不因评估移出而删这些运行必需依赖。
- 旧 CLI --mode、CHAOS_MODE、TUI /mode 旧别名、旧 provider/context/capability config、持久 ModeSnapshot 恢复应有现存/必要回归；不得改变模型能力、意图、授权各自语义。
- 修改 packaging/CLI/help/README 后跑相关 Feature 与 Root 检查，以及标准全套（动态 suites 的发现数和最后真实总计应对账）。CI 的 Windows 目前有 wheel install 检查，portable 只有源码编译/测试；改为所有目标平台也检查仓库外干净 wheel 是合理最小集成范围，不冒称本机验证等于跨平台 CI 已过。

## 最终监督条件

实施者提交精确 manifest/diff、原有 authentication hash、main/candidate 同步结果、实际命令 exit、wheel 前后清单/体积、独立评估 artifact、数据/资产保留报告与兼容测试。监督者随后独立开 wheel 与运行仓库外入口，才决定 PASS/CHANGES_REQUESTED。本审查未运行付费模型、未提交推送、未清理未知资产、未改产品代码。
