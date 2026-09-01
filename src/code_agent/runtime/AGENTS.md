# Execution Runtime
在策略授权后执行可流式、可超时、可取消的命令，并隔离宿主环境中的非必要能力。

## 边界
- 负责：以 Windows 10/11、PowerShell 和 Windows Terminal 为首版验收基线，实现本机 Runtime 与可选 Docker Runtime。
- 负责：环境变量净化、输出限额、超时、取消，以及由 Windows Job Object 持有的进程树终止。
- 负责：根进程以 `CREATE_SUSPENDED` 创建，在恢复前加入启用 `KILL_ON_JOB_CLOSE` 的匿名 Job；分配失败必须保持挂起、清理并失败闭合，不得 breakaway 或降级为未纳管执行。
- 负责：通过正式依赖 `psutil` 持有进程对象，并以 PID 与 `create_time` 共同验证待终止进程身份；该路径是 Job API 失败后的补充清理，不是正常终止主路径。
- 负责：以结构化事件报告命令、cwd、退出状态、stdout、stderr 和取消原因。
- 负责：PowerShell 脚本默认使用 `ErrorActionPreference=Stop`，未恢复的错误返回非零；显式 catch、`Continue`、`SilentlyContinue` 或 `Ignore` 表示脚本主动恢复；不得把 native stdout/stderr 送入对象管道，任意原始字节、无换行和控制符必须原样保留；真实 native stderr+0 不得当作 PowerShell 失败，末次非零 native code 优先传播，包装逻辑不得把脚本文本暴露到 argv 或展示命令。
- 负责：本机命令的 stdin 默认连接 `DEVNULL`，避免后台命令与 Windows TUI 争抢控制台输入；需要输入的数据必须由已批准脚本显式通过管道提供。
- 负责：应用启动时真实探测并冻结一个共享 PowerShell Runtime；显式 `powershell_7`/`windows_powershell_5_1` 严格匹配且不回退，兼容期 `auto` 才按 `pwsh`、`powershell` 顺序选择。
- 负责：为 Linux/macOS 保留 Runtime adapter 接口，但不把其端到端兼容性列入首版完成条件。
- 不负责：自行批准动作、读取未授权路径、持久化 API key 或编排 Agent 回合。
- 不负责：把本机 Runtime 描述为 OS 级沙箱或容器级隔离；它只提供受控的宿主进程执行。
- 不负责：猜测或自动改写 Bash、cmd 等其他 Shell 方言为 PowerShell。
- 负责：执行已校验的 typed verification argv；验证环境保持净化且不接受模型提供的脚本文本、环境变量、下载或安装参数。
- 不负责：把受信验证项目代码的间接文件或网络副作用宣称为已隔离；本机 Runtime 不是操作系统级沙箱。

## Units
- `RuntimeKind`、`ShellDialect`、`PowerShellSelection`、`StreamName`、`TerminationReason`: 提供 Runtime、Shell 方言、选择来源、输出流与终止原因的稳定枚举词汇 | 无副作用
- `ShellScript`、`PowerShellRuntimeInfo`: 冻结脚本文本方言和真实探测到的 executable/Edition/version | 无副作用 | Provider 提示只使用 executable basename；本地状态可显示完整路径和选择来源
- `PowerShellRuntimeResolver.resolve()`: 有界执行无 Profile 探测并缓存一个应用级结果 | 启动一次 `pwsh` 或 `powershell` 探测进程 | source workspace、task worktree、主/子 Agent 共用同一 resolver；显式方言失败不得迁移到另一方言
- `CommandSpec`: 冻结并校验 argv、legacy PowerShell 文本或 typed `ShellScript`、cwd、环境与执行限额 | 无副作用 | 三种命令载荷必须且只能提供一种；新脚本调用使用 typed 方言，legacy 字段只用于迁移兼容
- `OutputChunk`、`CommandResult`: 表达有界流式字节块及含相对 cwd、取消原因的最终命令结果 | 无副作用 | stdout 与 stderr 共用同一字节上限，`truncated_streams` 精确记录实际丢字节的流
- `decode_output(data, encoding, truncated): DecodedOutput`: 在文本边界按 BOM 或显式编码严格解码 Runtime 原始字节 | 无副作用 | 默认 UTF-8；失败保留完整 Base64，逐流区分未知/混合编码与被输出上限截断的多字节尾部
- PowerShell 双脚本包装：UTF-8 BOM payload 保留顶层 `using`/`param`/`return`，独立 UTF-8 controller 配置 Console 与 `$OutputEncoding`、以默认 Stop + try/catch 传播未恢复错误和 native exit | 创建两个权限收窄的临时脚本并在所有退出路径清理 | 不拦截 PowerShell 对象流；用户脚本文本不进入 controller、argv 或展示命令；显式 `exit` 仍由 PowerShell 自身直接决定进程退出
- `RuntimeErrorBase`、`RuntimeUnavailable`、`RuntimeStartError`、`ProcessTreeTerminationError`: 区分 Runtime 缺失、命令启动失败与无法证明进程树已终止 | 无副作用
- `DirectoryLease(path, guard)`: 在进程创建期间锁定并复验工作目录的最终路径 | 临时持有 Windows 目录句柄 | 不共享 DELETE；进程创建返回后立即释放
- `capture_process_identity(pid, process_api): ProcessIdentity`: 进程以 `CREATE_SUSPENDED` 创建后立即绑定 psutil Process 对象及其 `create_time` | 查询宿主进程 | 终止阶段不得按 PID 重新绑定
- `resume_process_identity(identity, process_api): None`: 复核启动身份后恢复挂起进程 | 恢复宿主进程执行 | 必须在 DirectoryLease 释放前同步完成
- `WindowsJob.create()`、`assign(pid)`、`terminate()`、`close()`: 创建并独占一个不可继承的匿名 Job，设置 `KILL_ON_JOB_CLOSE`，用最小进程权限分配挂起根进程并统一终止后代 | 调用 Kernel32 Job API | Windows 10/11 可嵌套 Job；AccessDenied 或受限宿主 Job 不得静默回退，所有句柄显式关闭
- `terminate_process_tree(process, process_wait, root_identity, job): None`: 运行中根进程先同步终止所属 Job；正常根退出则只在 Job 尚有后代时终止，并有界等待 `ActiveProcesses == 0`；Job API 失败或根进程逾期才使用身份绑定的 psutil 树做补充清理 | 终止宿主进程 | fallback 即使清净也必须报告原始 Job 控制面失败；最多跟踪 1024 个身份
- `WindowsLocalRuntime.run(spec, cancellation, on_output): CommandResult`: 在净化环境中以 `DEVNULL` stdin 挂起启动、捕获身份、加入 Job 后恢复 Windows 进程，再处理流、deadline、取消、PowerShell/native 退出状态与进程树终止 | 启动和终止宿主进程 | Job 归零并关闭后，两个 pipe reader 还必须在有界时间内到达 EOF 才可返回成功；Windows-first；本机执行不是 OS 级沙箱
- `DockerRuntime.run(spec, cancellation, on_output): CommandResult`: 以固定 bind mount、workdir、`--pull=never` 和默认禁网参数调用已有 Docker 镜像 | 启动 Docker CLI 和容器 | typed 脚本只接受 `posix_sh`；不检查或拉取镜像，环境值不进入 argv
