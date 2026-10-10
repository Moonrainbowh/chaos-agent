# S15 阶段报告

状态：DONE（第二候选）。16路径 patch `9fa331043d6596c6c1a68c1b9bb1e5458390ce49cfb3b5efff112cc535d47c21` / tree `c3a3094d4b557926d081a4f2accdf20caebb47a8`，基线S14 tree `663bb5a555bd0c5bbbdec3d94bd845272b22f7fb`。正确来源 wheel 的 Python3.10/3.13仓库外验收通过；第二标准完整测试实际exit0，30套3440发现=执行、30skip、零失败错误遗漏。结束核验实际0，独立审查者运行最终门控并出具supervision.md/json正式PASS，允许进入S16。

## 实现与兼容

运行 wheel 排除仅开发的 code_agent.evaluation、6个 continuity 模块及 context_experiment_host；独立 benchmarks 分发补回相同旧导入，不覆盖 runtime 的 __init__ 或生产 Unit。源码/测试仍在原位，标准发现不会遗漏 Evaluation。开发 sdist 物化源码，可在仓库外重建。构建使用自有临时目录，过滤 setuptools 将被排除包当 namespace data 重新带入的路径；不清理用户 build/lib。作者与独立监督均实际测试 stale build 和 sdist→wheel。

CLI 仅将 --mode 四档标为高级兼容入口，解析与环境别名不变；TUI 已有注册表分层，无需另建菜单。README 从大段配置说明缩成入门流程，高级内容集中 docs/advanced-configuration.md；默认 capability/context 策略不再要求用户理解，模型/effort/意图/授权各自独立。纠正迁移参考内 structured verification 的旧默认说明。

运行依赖明确限定已经验证私有兼容的 MCP SDK 1.29.1，并声明 Host 直接使用的 jsonschema/referencing。uv offline 更新锁，依赖版本仍与既有 lock 一致。CI 五个矩阵增加仓库外干净 runtime/add-on 安装与离线检查；本机没有触发新远程 CI，不把配置当已通过。

## 实际验证与修正

修正源码来源后的 baseline runtime 1,244,751 bytes/675成员/展开4,342,397 bytes；候选1,159,789/637成员/展开4,099,334，压缩减少84,962 bytes（约6.8%）。仅比较产品 wheel，不计依赖环境。开发分发71,379 bytes，38个保留源码原始字节相同。最终物证见 wheel-before/after.json。

CLI/bootstrap/旧 mode/只读历史27项、Config默认与旧别名/context/capability30项实际 exit0。主 Agent与独立监督分别在库外新venv验证 runtime：4 console entrypoints help/version、3静态/许可资源、导入来自新venv、无开发模块、owned SQLite history/task list/result、pip check、MCP1.29.1，通过且无Provider/Shell任务执行。

主 Agent首次离线安装缺缓存失败；候选venv没有pip导致第一次下载未执行。分别留日志，改用同Python3.13 base pip下载uv锁定带hash的wheel后新环境安装成功。未共享原venv或editable安装。

另发现前构建脚本从main复制全部源码，混入原有个人authentication改动；这批wheel/descriptor/安装日志完整归档initial-candidate/main-source-builds，不用作候选物证。已改为隔离candidate源码、仅overlay明确S15产品，每一个runtime .py都与candidate逐字节核验；authentication从未同步/改写，标准全套始终跑隔离candidate。最终重新建包与安装验收，以正确来源的新artifact为准。

开发 wheel 安装、v1/v2离线参考/反例自检实际通过；真实Host A组失败：首窗为稳定虚拟first_window_id，SQLite真实window行0，旧评估误期望1。public/hidden verifier与fresh evidence已成功，但不以这些局部通过掩盖整组失败。initial-candidate保存14路径raw/patch、安装失败报告及CHANGES_REQUESTED监督。

独立诊断后修正Host评估_record：A要求0真实持久行，B/C/D继续3；新增window_metrics_version=2与virtual标记，保留旧历史不重算。没有新增生产window行，没有把虚拟ID冒作committed，没有修改grader/oracle、可信验证或用量门。真实A测试核所有receipt稳定首窗、8轮/24工具、public/hidden/fresh/cleanup；异常A多一窗、B/C/D缺窗仍拒。连同原D重启等共4项实际通过。新发行物须复建，旧冻结全套不能当最终证据。

## 资产与授权

28个跟踪日志/预览资产749,610 bytes按历史证据保留。git worktree列表及未知目录只记录、不删除；不存在足够归属证据的资产不做无差别清理。资产清单不含正文/凭据，删除数、worktree退役数与数据迁移数均0。原authentication三路径、原8787 Host不变。未提交、推送、合并、发布或调用付费模型。

本次自行下载的两个wheelhouse共48,650,092 bytes已外置到 `C:/Users/Windows11/.codex/artifacts/next-version-s15-9f82104aaf7c`。迁移前所有成员必须为wheel、SHA均匹配完整uv锁导出，源/目标绝对路径限定于本阶段目录和新建拥有目录；迁移后逐文件重核SHA，原缓存目录不存在。没有删用户资产或原worktree。所有权、成员、体积和复验缓存路径见dependency-cache-location.json；离线复验应使用其中wheelhouse/wheelhouse310的新绝对位置。

## 完成条件

新16路径与新开发wheel raw一致，真实干净安装/A arm全退出0，标准30套最终数量对账、认证原diff保护和独立最终PASS后才DONE。Linux/macOS新CI、真实模型及整体发行仍属于S16所需外部验证/授权边界。

第一14路径标准全套实际exit0、3438run/30skip/零失败错误遗漏，但其开发安装A组失败，因此不放行；完整日志及退出归档initial-candidate。新16路径增加两项真实/负例测试，最终期望3440项，不能混用第一全套成绩。Python3.10新venv与3.13相同入口/资源/历史、pip check、v1/v2参考自检与真实A组全部实际exit0，日志runtime-clean-install310及final。旧pip23对已固定pyjwt的crypto extra二次解析引起hash下载失败，保留失败日志；从完整uv导出清单逐项--no-deps下载，固定版本/hash不放宽，安装仍完整解析且pip check通过；CI也采用这一下载方式。
