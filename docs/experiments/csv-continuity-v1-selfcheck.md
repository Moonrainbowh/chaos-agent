# CSV 连续任务：离线案例自检

模式：offline-scripted-reference；真实 API：NOT_RUN。
窗口与进程身份来自测试 double，不代表真实换窗或恢复已经验收。

| 检查 | 变体 | 预期 | 实际 |
|---|---|---|---|
| 参考实现 | A | 通过 | 通过 |
| 参考实现 | B | 通过 | 通过 |
| 参考实现 | C | 通过 | 通过 |
| 参考实现 | D | 通过 | 通过 |
| unrepaired | A | 拒绝 | 已拒绝 |
| ordinal-stale-assumption | C | 拒绝 | 已拒绝 |
| rewrite-output | A | 拒绝 | 已拒绝 |
| revert-external-change | C | 拒绝 | 已拒绝 |
| stale-validation | C | 拒绝 | 已拒绝 |
| 旧序号实现，改版前 | B | 通过 | 通过 |

整体自检：通过。
完整 verifier、事件和失败原因见相邻 JSON。API token 与记忆工具指标未测量。
