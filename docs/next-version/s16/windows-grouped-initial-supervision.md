# Windows 根模块分组初次独立监督

结论：**CHANGES_REQUESTED**，仅针对正在 main 实现的监督分组；未修改候选或产品。

审查新增 source manifest、group aggregation、受监督 discovery、CI Windows 单 flag 和原 Windows Job gate/cleanup 路径。默认与 POSIX 不拆组、每组仍 600s、预发现仍在同一 supervisor 子进程，外层根仍一个逻辑 suite。

独立反例脚本为 `grouped_independent_probe.py`。初轮 5 例中 2 例失败；补充 IPC 反例后的 `grouped-independent-ipc-first.log/.exit` 为 **6 例、3 失败、实际退出 1**：

1. 成功组没有结构化 result counts，仍返回 0 / totals None。
2. 成功组 counts.discovered=0，但 manifest 包含一个实际 ID，仍返回 0。
3. `coverage=False` 的嵌套 supervisor 继承父 `CHAOS_TEST_COVERAGE`，使用父所有的 IPC 文件路径；本次应清除继承项后按 opt-in 分配自己的文件。

另外三例独立通过：成功组遗漏 run ID 被拒绝；失败模块后仍运行后续模块且 ID 并集完整；真实 subprocess 中模块 A 的导入环境修改不会泄漏到模块 B 的执行进程。

修复要求：成功缺 counts 必须失败；counts 字段、非负整数、discovered 与本模块 ID 数一致均严格核验；畸形 counts 不参与完整总数汇总。只修上述监督边界，保留期限、Job/清理、原测试断言。收敛后再保存独立复验报告，初次失败物证不覆盖。本报告不宣称完整 30 套、CI 或 S16 总体通过。
