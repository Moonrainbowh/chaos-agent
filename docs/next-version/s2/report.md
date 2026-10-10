# S2 测试发现与可重复基线

状态：DONE。独立监督最终 PASS，候选 d4f8fe8 的五矩阵 CI 与源码/wheel 安装验收全部满足；允许进入 S3。

## 修改范围

基线沿用 S1 的 main/5793c72 和用户 authentication 差异。最初产品/配置共 11 个文件；目标 CI 暴露测试平台装配问题后，增加 1 个测试文件（合计 15 个，另包含 Windows 大文件身份 Schema 修复及契约）；完整 scoped diff 见 implementation.patch（不含原有 authentication 变更）。未实现 S3—S16。

- Sessions 实现阶段：仅将 test_memory.py 的现有断言接入 unittest.TestCase，临时数据库用 context manager 关闭。原来的业务断言全部保留；实际发现执行 1 项通过。
- Runtime 实现阶段：默认 UTF-8 失败不再静默猜 Windows ANSI；显式 windows-ansi/windows-oem 与 BOM 处理保留。新增受控 cp936/cp1252 与 cp936 截断反例，15 项编码测试通过；只同步 decode_output 契约。
- 集成阶段：runner 发现 28 Feature + remote + 根集成共 30 套件。普通失败/超时继续；清理/进程监管异常中止并列未运行项；零发现和未适配的模块级测试函数报错，不能成功。
- 子 runner 用独立 TestLoader，不共享默认 loader 的 discovery root；输出 discovered/run/skipped/failures/errors/expected failures/unexpected successes，父 runner 汇总 JSON。只让实际仓库 runner 写 IPC 汇总，嵌套临时 suite 不覆盖最终文件。
- 首审发现函数防漏只扫描顶层，正常TestCase掩盖子包函数。改为遍历root及带__init__.py的可导入子包，与unittest递归范围对齐；增加混合包反例，不通过数量凑数。
- CI 两个 job 使用 uv 0.12.13、矩阵指定解释器、uv.lock 与 dev group；Windows wheelhouse 从锁文件导出全部运行依赖，不再维护不完整手写列表。纠正 AGENTS.python.md 中默认 300/实际 600 的文档漂移。

## 复现与自测

每条命令以实际解释器路径执行，未依赖 PATH 的 python。

- `memory-test.log`：标准 unittest test_memory.py 发现 1 / 运行 1 / 通过。
- `encoding-before.log`：修复前受控 cp936 反例失败；`encoding-after.log`：15 运行、0 失败。
- `locked-runner-tests.log`：锁定依赖环境 runner/timeout 选测 27 通过，含真实进程超时清理、零发现、清理失败列出未运行项。没有增大 timeout 或跳过测试换绿。
- `runner-nested-fix.log`：首审修复后重新运行全部runner/timeout选测28通过。`current-discovery.json`按当前树逐套件独立进程核验发现与函数防漏范围，明确不执行断言；不将发现数当通过数。
- `fault-probe.log`：真实受监管故障注入，first 故意失败，last 写入执行标记；两套件都执行、总 run=2/failures=1、最终退出码1。故意失败不是产品失败，脚本自身断言 PASS。测试文件与工作区随后自动清理。
- `full-regression.log`：旧 .venv 的第一轮完整运行 30 套件，根集成中本轮新增的空套件测试暴露共享 TestLoader 的嵌套 discovery 问题。已修正为独立 loader，新增确定性回归。不是成功证据，也不合并成最终通过。
- `locked-full-regression.log`：独立 CPython 3.11.15、锁定依赖运行的完整回归，发生在首审子包防漏修复前；final-summary.json：30套件，发现3031、运行3031、跳过30、失败0、错误0、未运行套件0，退出0。修复后的新回归由上述28选测和全范围发现核验覆盖，不将3031写成当前最后树的全量通过。运行数包含跳过。用户authentication差异仍存在，CI候选应只提交本次范围。
- 没有复现既有审查的 RuntimeStartError/await None 回归，因此未凭旧报告修改对应 runtime；最终全量若出现再按证据处理。

## 安装和环境

本轮独立 wheel 环境路径在 clean-wheel-env-path.txt，源码安装环境在 clean-source-env-path.txt。没有修改原 .venv。

旧 .venv 的 pip check 检查安装 metadata 能通过，但其 starlette=1.6.0 与当前 pyproject 的 `<1` 不符，不能作为可复现的发行依赖。最终环境按 uv.lock 导出的带哈希 requirements 安装；starlette=0.52.1，全部 47 个已安装包兼容（含 dev 工具）。完整环境由锁和安装日志保留，不将项目上的旧 metadata 当源声明验证。

直接 PyPI 在线下载遇到 TLS EOF，重试 native/system cert 后仍失败。复用缓存完成兼容 wheel 安装后，以清华 PyPI 镜像取得锁定缺项，导出文件的 SHA256 校验保留；镜像仅用于本轮隔离安装，没有改 uv.lock 源、用户配置或 CI registry。依赖来源差异和失败日志保留，未关闭 TLS 验证。

- `final-build.log`：wheel/sdist 构建成功，产物在 final-dist。版本仍 1.0.3，未发布。
- `final-wheel-check.log`、`source-check.log`：两个隔离安装环境依赖检查通过。
- `final-installed-package.json`、`installed-source-package.json`：仓库外 cwd 实际 import 来自各自 site-packages；静态页面40489字节；四个 console_scripts 齐全；installed output_codec SHA256 与源码一致，默认 UTF-8 反例通过。
- CLI `chaos-agent --version` 输出 1.0.3；没有把本轮 wheel 安装到用户常用环境，也未请求真实 Provider。

## 独立监督、CI 与回退

独立监督将重跑故障注入和关键反例并检查 diff/CI。只有本地 + 目标平台结果与监督全部满足才可标 DONE。

首审CHANGES_REQUESTED的子包漏测证据已复制为s2-independent-nested-probe.py/log；独立故障注入、Memory、编码、runner与超时继续选测通过，见s2-independent-audit.log。重审 PASS，见 s2-independent-discovery-recheck.log、s2-independent-nested-recheck.log、s2-independent-audit-recheck.log；独立执行 28 项选测通过，当前 30 套件发现 3032 项（仅发现检查）。没有把主Agent全量日志写成监督者亲跑。

用户已授权候选分支提交/推送。隔离工作树从 5793c72 建立，提交 ee49e03363d214e6c45a082f16536237d4a27dee 仅包含上述 11 文件；分支 codex/next-version-s2-baseline 已推送。候选使用原始 authentication 代码，53 项 authentication 和 22 项 runner 测试通过。CI run 37174758881 覆盖 Windows Python3.10/3.13、Ubuntu3.10/3.13、macOS3.13，正在运行，链接 https://github.com/Moonrainbowh/chaos-agent/actions/runs/37174758881 。未合并、未发布；目标平台结果尚不视为通过。

兼容变化：使用默认 UTF-8 接收旧 ANSI 字节时，将显示 unknown/base64，调用方应显式选择 ANSI；不再静默猜测。恢复/权限/预算模型未变。回退可只撤销本次11文件 diff，authentication 用户差异和用户数据库不在回退范围。



## 目标 CI 首轮与平台修复

首轮 ee49e03 / run 37174758881：Ubuntu 3.10/3.13 与 macOS 3.13 均实际执行 30 套件、3032 项，跳过191、失败3、错误0、无未运行套件。失败均为 test_pi_conversation_controls.py 无条件装配 WindowsLocalRuntime，与其 POSIX 命令分支不符。仅修测试 fixture 按平台装配对应 Runtime，保留全部执行/取消断言；Windows5项相关测试及独立监督通过。增量提交 61aa64410e48c2a424e441e455cf72c4b27c64ed 已推送，第二轮 CI 37175030195 正在执行。初轮日志 ci-ubuntu310.log、ci-ubuntu313.log、ci-macos313.log，不能视为PASS。目标CI结果仍待完成。


## Windows 3.13 身份兼容修复

首轮及第二轮 Windows 3.13 均在 batch code slice 测试出现3错误/1失败。Schema将device/file ID限制在signed64，而Python3.12起st_dev可unsigned64、st_ino可unsigned128（官方 https://docs.python.org/3/whatsnew/3.12.html ）。本机NTFS低ID原4测试通过，不冒称本机复现了CI文件系统；新受控高ID反例修前失败。仅扩大这两个身份字段的精确整数范围，身份前后比较保持完整，不截断、不跳过权限/stale。新反例验证unsigned最大值可进入stale拒绝，超过上限继续拒绝；Windows3.11/3.13选测通过，独立监督5slice+11schema通过并审查PASS。契约同步1行。

提交 d4f8fe8629ba225b6c02278b475b380a82fbb7db 已推送，第三轮CI37175527899待完成；candidate-final.patch SHA256 addfd91cbcdf98d99a1cbc882ab23df0ae13f2b16840fe3c933ed6c347a87d6f，candidate-files.txt列15文件。新版candidate-dist wheel/sdist构建成功；两个隔离安装均更新到候选，仓库外check_installed.py验证静态资源、入口、严格UTF8与完整身份Schema通过，见candidate-installed-wheel.json/source.json。未发布。


## 最终目标 CI 验收

提交 d4f8fe8629ba225b6c02278b475b380a82fbb7db / run37175527899 已 completed/success，五矩阵全部成功；完整元数据candidate-final-ci.json，平台日志及汇总ci3-<platform>.log / -summary.json。

| 平台 | 套件 | 发现/运行 | 跳过 | 失败/错误 | 未运行 |
|---|---:|---:|---:|---:|---:|
| Windows Python3.10 |30|3033/3033|17|0/0|0|
| Windows Python3.13 |30|3033/3033|17|0/0|0|
| Ubuntu Python3.10 |30|3033/3033|191|0/0|0|
| Ubuntu Python3.13 |30|3033/3033|191|0/0|0|
| macOS Python3.13 |30|3033/3033|191|0/0|0|

运行数包含跳过；跳过不是执行通过。Windows两job的构建、锁定runtime依赖下载、离线wheel安装、pip check及import均success。候选未合并/发布。独立监督 /root/s2_supervision 最终 PASS，亲自核对五 job 日志、候选 diff 和安装产物；S2 放行。非阻断建议：超时总计为 null 时，后续可补已完成套件小计。
