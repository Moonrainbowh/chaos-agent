# 授权预算调整与真实试跑

用户明确授权默认工具额度20,000、总提示预算300,000 token，真实模型GLM5.3-flash/medium。提交4ed1a6a9d7ab315168ced21d14174638fea60e8c，tree08d47ac626fc847155c14f628ec2fa41985fd988，15个精确路径已提交推送候选分支；个人authentication没有加入。

Context Feature189项、真实Host目录3项（四策略×两拓扑×Git/nonGit、initial/full）、模型窗口预算5项实际退出0。显式2,000仍拒绝Git初始目录及非Git单项披露；显式WindowPolicy work_tokens65,536仍限制其prompt，不因新默认扩大。规则6,000、系统2,000、消息12,000、Repo Map2,000等子额度保留；300,000是总上限，不是每次请求的实际用量或费用。

首次实际CLI记录已另存300k-service-off-attempt：context_built预算300,000，实际本地估算3,293、工具1,977、安全预留1,664，随后model_started。请求因ModelStreamError中断，退出4；文件未变，verification unknown，Provider usage不完整。未用模型文本或本地token估算代替验收。

独立无生成HEAD探针provider-connection.json记录当时127.0.0.1:7863 ConnectError，generation_requests=0。用户随后明确确认已启动并要求重试；真实GLM调用已恢复。模型/profile/medium与五项固定测试没有更换。

## 已核验的真实CLI样本

实际结果及物理hash/独立测试见real-cli-physical-validation.json，每次任务使用自有目录/状态库。

| 样本 | 实际结果 | Provider记录 |
|---|---|---|
| 只读分析 | exit0，completed/unchanged/unverified；所有文件hash不变 | 2请求，input7475/output449，完整 |
| 普通修改v2 | exit0，completed/changed/verified；只改names.py，系统run_verification五测试及独立五测试通过 | 4请求，input16355/output194，完整 |
| 无需修改v1 | exit0，completed/unchanged/unverified；所有文件hash不变 | 2请求，input7385/output151，完整 |
| 无验证器文档 | exit3，waiting_decision/changed/unverified；notes.txt内容精确匹配 | 2请求，input7148/output126，完整 |
| 受保护.env | exit3，waiting_decision/unchanged/unknown，stop_reason=approval required；文件hash不变 | 3请求，input10730/output308，完整；仅证明审批阻断，尚非操作者明确拒绝 |

普通修改首次无项目manifest：模型改代码且通过普通execute五项测试，但系统不能discover结构化验证器，exit3/unverified；完整旧尝试保存modify-no-manifest-attempt。fixture v2只补固定pyproject声明，任务/模型/五测试保持原样，并先离线断言官方discover_projects识别Python unittest。重跑后有system-local_milestone run_verification回执，才计为verified。普通Shell exit0没有冒充系统验证。

MCP兼容补充：两版本33项及全新进程真实SDK默认15/60/6秒冷启动/调用/清理通过；独立审查者也复跑deadline及冷启动。审查者随后用量限制中断，没有正式最终PASS，且上述预算调整发生在其初读之后。最终独立监督仍待完成。

旧CI37483688776已完成失败：Linux3.13通过；Linux/Windows3.10缺asyncio.timeout和TestCase.enterContext，Windows/macOS3.13冷启动故障测试超时；两Windows根套件另有600秒总期限超时，保留各日志。兼容修复不加期限不跳过测试；新完整本地/CI验证尚在进行，不能宣称S16完成。旧candidate-manifest/wheel仍是此前冻结物证，不作为新提交发行物。
