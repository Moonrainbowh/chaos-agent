# Execution Runtime
在策略授权后执行可流式、可超时、可取消的命令，并隔离宿主环境中的非必要能力。

## 边界
- 负责：本机 Runtime、可选 Docker Runtime、环境变量净化、输出限额、超时和进程树终止。
- 负责：以结构化事件报告命令、cwd、退出状态、stdout、stderr 和取消原因。
- 不负责：自行批准动作、读取未授权路径、持久化 API key 或编排 Agent 回合。
- 不负责：声称本机 Runtime 提供容器级隔离。

