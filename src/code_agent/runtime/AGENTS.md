# Execution Runtime
在策略授权后执行可流式、可超时、可取消的命令，并隔离宿主环境中的非必要能力。

## 边界
- 负责：以 Windows 10/11、PowerShell 和 Windows Terminal 为首版验收基线，实现本机 Runtime 与可选 Docker Runtime。
- 负责：环境变量净化、输出限额、超时、取消和 Windows 进程树终止。
- 负责：通过正式依赖 `psutil` 持有进程对象，并以 PID 与 `create_time` 共同验证待终止进程身份。
- 负责：以结构化事件报告命令、cwd、退出状态、stdout、stderr 和取消原因。
- 负责：PowerShell 脚本执行必须保真传播末次原生命令退出码及 PowerShell 失败状态；包装逻辑不得把脚本文本暴露到 argv 或展示命令。
- 负责：为 Linux/macOS 保留 Runtime adapter 接口，但不把其端到端兼容性列入首版完成条件。
- 不负责：自行批准动作、读取未授权路径、持久化 API key 或编排 Agent 回合。
- 不负责：把本机 Runtime 描述为 OS 级沙箱或容器级隔离；它只提供受控的宿主进程执行。
- 不负责：猜测或自动改写 Bash、cmd 等其他 Shell 方言为 PowerShell。
- 负责：执行已校验的 typed verification argv；验证环境保持净化且不接受模型提供的脚本文本、环境变量、下载或安装参数。
- 不负责：把受信验证项目代码的间接文件或网络副作用宣称为已隔离；本机 Runtime 不是操作系统级沙箱。

## Units
- `RuntimeKind`、`StreamName`、`TerminationReason`: 提供 Runtime、输出流与终止原因的稳定枚举词汇 | 无副作用
- `CommandSpec`: 冻结并校验 argv 或脚本命令、cwd、环境与执行限额 | 无副作用 | argv 与 PowerShell 脚本必须且只能提供一种
- `OutputChunk`、`CommandResult`: 表达有界流式字节块及含相对 cwd、取消原因的最终命令结果 | 无副作用 | stdout 与 stderr 共用同一字节上限
- `RuntimeErrorBase`、`RuntimeUnavailable`、`RuntimeStartError`、`ProcessTreeTerminationError`: 区分 Runtime 缺失、命令启动失败与无法证明进程树已终止 | 无副作用
- `DirectoryLease(path, guard)`: 在进程创建期间锁定并复验工作目录的最终路径 | 临时持有 Windows 目录句柄 | 不共享 DELETE；进程创建返回后立即释放
- `capture_process_identity(pid, process_api): ProcessIdentity`: 进程以 `CREATE_SUSPENDED` 创建后立即绑定 psutil Process 对象及其 `create_time` | 查询宿主进程 | 终止阶段不得按 PID 重新绑定
- `resume_process_identity(identity, process_api): None`: 复核启动身份后恢复挂起进程 | 恢复宿主进程执行 | 必须在 DirectoryLease 释放前同步完成
- `terminate_process_tree(process, process_wait, root_identity): None`: 冻结并有界终止根进程及后代，先复核启动身份及每个后代的 `create_time` 再发出 kill，并确认无存活身份 | 挂起并终止宿主进程 | 最多跟踪 1024 个身份；身份变化、AccessDenied、deadline 或 survivor 均抛出结构化错误
- `WindowsLocalRuntime.run(spec, cancellation, on_output): CommandResult`: 在净化环境中挂起启动、捕获身份并恢复 Windows 进程，再处理流、deadline、取消、PowerShell/native 退出状态与进程树终止 | 启动和终止宿主进程 | Windows-first；本机执行不是 OS 级沙箱
- `DockerRuntime.run(spec, cancellation, on_output): CommandResult`: 以固定 bind mount、workdir、`--pull=never` 和默认禁网参数调用已有 Docker 镜像 | 启动 Docker CLI 和容器 | 不检查或拉取镜像；环境值不进入 argv
