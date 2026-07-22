# Workflow DAG
把 Host 已观察到的任务、子 Agent、验证和交付生命周期投影为可恢复、可审计的有向无环工作流。

## 边界
- 负责：定义不可变 Workflow、节点、边、状态转换和快照模型。
- 负责：验证节点 ID、边类型、无环性、终态单调性及父子任务关联，不接受模型自由文本作为状态写入。
- 负责：把 typed Host observation 幂等归约为节点状态，并在恢复时对未终结节点执行保守对账。
- 负责：向 Interfaces 提供 Host-neutral 的只读快照和过滤信息。
- 不负责：调度 Agent、执行工具、判定验证成功、渲染终端、分配 thread 权限或替代 Sessions 事务。
- Workflow edge 只表达执行依赖和可视关系，永远不授予 thread 读取、工具或文件权限。
- 失败、取消和部分完成必须保留原始节点历史；模型不能直接创建成功、验证或交付终态。

## Units
- `Workflow`、`WorkflowNode`、`WorkflowEdge`、`WorkflowSnapshot`：表达不可变执行投影、状态、引用和三类边 | 无副作用 | 时间统一为 UTC，引用有界且 Workflow 归属一致
- `WorkflowGraph.add_node`、`add_edge`、`transition`、`snapshot`：维护单个 Workflow 的 DAG、合法状态机和不可变快照 | 进程内状态 | 拒绝缺失端点、自环、多节点环、跨 Workflow 和终态重启
- `WorkflowService.observe(HostObservation)`: 把任务、子 Agent、验证、Evidence 失效和交付的 typed Host observation 幂等投影为持久 DAG | Workflow Repository I/O 与快照通知 | 不接受模型 dict，只有保存成功后发布
