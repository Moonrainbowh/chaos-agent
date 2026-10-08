# S4 独立监督

状态：PASS。产品约束与独立功能验证通过；根全量587项通过，原始归档来源已纠正并复核。S4可放行进入S5，本监督不实施其它阶段。

## 审查快照与范围

S3 基线 tree：`9e2cf1ac462a894a756f0065a9153fbb165de31d`。本次 S4 patch SHA256：`036f854eafbba4a1c41e6bd32e912606dbd13a4d082d51f33931d2285ac06107`。

在隔离候选 `C:/Users/Windows11/.codex/worktrees/next-version-s2-baseline/chaos-16-agent` 对 snapshot.json 全部20份文件逐个 SHA256 核验一致。未修改产品源码、其它 S 阶段、authentication 文件，未提交或推送。

通读正式 root/Python/Core/Interfaces/Host、相关 Context/Thread Intelligence 契约和参考快照；逐类对照 constraints-audit.md，并直接核对原 Units 的必要条件。采用当前明确边界处理四处历史冲突：隐藏 /mode、现代单栏、wide 仅尾部动态、启动临时屏幕与随后清理的阶段区分，没有借字数目标删除约束。

## 必需约束逐类检查

| 类别 | 默认正式文本核查 |
| --- | --- |
| 阶段/目录/物证 | 需求→实现→集成权限、Unit职责、真实受阻/产物核验、扩展维护条件保留；迁出模板与重复说明不构成默认规则。 |
| Python | 300行目标/50行拆分信号、复杂PowerShell脚本落盘、全量runner发现/600s/2s/124/清理失败中止、实际检查命令保留。 |
| Core预算/取消 | 模型及动作前持久预算检查、耗尽零调用、跨恢复累计、软额度≤硬额度、独立run active-time、语义调用计费；tool结果及消息持久后取消、开始写先可恢复记录保留。 |
| Core身份/证据 | Host确定性解析不扩权、unknown不算进展、ask/plan禁写/execute、可信task facts、不可变request、revision一次build、显式partial、SUPERSEDED终态、L0阻同批/最终tests→build保留。 |
| Core数值/披露 | 参数校验连续3次、tool-only5回合、总结工具误调用仅一次重试、schema digest失效、peer空allowlist、完整附件引用只user与本地数值事件白名单保留。 |
| UI控制/审批 | 单前台、Enter持久排队/Tab安全边界转向、Esc pausing→checkpoint、关闭durable收尾、两秒Ctrl+C/已paused单次、默认拒绝/一次性选择/可取消、计划Diff禁止评论/发送/刷新保留。 |
| UI附件/凭据 | digest回执清稿、失败及后续新附件保留、批预算/幂等/无孤儿blob、稳定imageN不重排、标记不进正文；隐藏凭据不入历史/附件/转录、登录禁任务提交保留。 |
| UI展示/具体数字 | auto≤64列、四列触控、6行工作台、65536 UTF-8整次拒绝、粘贴>10逻辑行/Unicode码点、RGB30/48/76、19 primary、280ms/≤160ms/≤30fps/三字符点阵、map1..50/0..100000保留。 |
| UI隐私/真实性 | 无raw reasoning、不可信控制码清洗、partial不是证据、未知usage/价格保持未知、缓存不重复、token/s首delta、只typed状态、候选非根因、只本地结构化预算诊断保留。 |
| Host执行/授权 | 过滤后compact、旧名仅已披露operation、双层plugin策略、MCP opaque、副作用前schema/preflight/policy、Web启用不授网络权保留。 |
| Host workspace/恢复 | auto/direct无无因Git探测、required isolation先thread/task、同source单写者、敏感opt-in、legacy missing-only只读、不自动迁移/GC、回收多重闸门、canonical root各gate、PRE/POST/foreign conflict、加锁重读、batch先rewind保留。 |
| Host数字/冻结/共享 | slice1–16/device64/file128、list25/50、marker≤512直属、local lineage最多4常数Git、CLI130、runtime专用≤300、完整ModeSnapshot digest、Anthropic medium prompt-only、共享同代RepoIndex、memory/structured-verification默认关闭保留。 |
| Host附件/实验/生命周期 | 动态profile modality与旧factory兼容、checkpoint仅八元数据、partial client BaseException回收不盖原错、close幂等不删状态；20轮/100工具明确属评测且其unknown usage/完整工具组/固定profile约束保留。 |

## 独立执行

使用项目 `.venv` Python，隔离候选真实导入与生产装配；离线 Fake 仅替换模型，不能作为 Provider usage 或 API 成绩。

- Context 全套188、Thread Intelligence全套36、UI诊断+默认阻断+budget集成共13：全部通过。
- 完整默认 create_application wrapper、真实模式/system提示及六个工具 `load_tool_contract/read/search/write/edit/execute`，根/Core/Interfaces/Host四次模型回合通过；每份加载规则全文均存在。system本地估算2667/4729/7045/6165，总提示上限20000，默认规则6000。物证：supervision-default-host-contexts.json。
- 独立9个反例：规则token、规则总字节、system+rules、真实tool schema、总提示不能保留minimum消息；除tool仅适用于自动构建外均分别走自动与手动compact。全部摘要调用0、checkpoint0、用户文件原样；终端含本地用量/相对规则名/显式修复方法，不含原规则正文或绝对路径。物证：supervision_probe.py、supervision-counterexamples.json。
- 原规则未摘要替换或截断；preflight在外层摘要/发布checkpoint前，最终build再次检查。现有context_windows先scaffold路径未被扩围修改。旧内层接口无preflight继续兼容。

## 全量结果、来源纠正与限制

已读取 root-final-tests.log 的真实末尾：根集成587 discovered/run，errors/failures/skipped均0，223.681s，exit_code0。此前旧fixture失败保留独立日志，不抹去失败历史。原fixture只因默认rule cap变更失去“超过rule cap但system组合仍合法”含义；1900→3900恢复相同边界，原断言不变，独立5项已包含在上述13项中通过。本报告不将根587称为全部Feature总量；Context/Thread的独立全套数量见上。

归档来源初查发现Interfaces reference比S3多本次BudgetDiagnostic行；已反馈并纠正。reference_baseline.py从S3 tree逐字节保存五份*-before.md，原物理中间态保为*-precompression.md，reference-manifest.json区分字节/逻辑行一致性。监督复核五份完整逻辑行均与基线一致，四份中间态仅CRLF差异、Interfaces还含新诊断条款；正式产品20文件hash与功能结果保持同一快照。全部引用物理可访问，参考文本不属于默认RuleLoader注入链。

非阻断可用性记录：真实tool schema超限时UI报告tool_tokens，未列max_tool_tokens；硬检查仍拒绝且总预算/规则诊断完整。该项不扩大本次实施范围。
