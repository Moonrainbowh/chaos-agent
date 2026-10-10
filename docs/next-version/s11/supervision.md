# S11 独立监督

最终状态：PASS。第三次最终候选 34 路径、patch `859ed1e22b1bd24b51661a1450e5613a86d61cc6f0010319bd97b70a4ab96abd` 已完成独立验收及标准全套，S11 可放行进入 S12。下文保留早期失败与修复记录，不将其算作通过。

最终证据：标准 30 套 3320 discovered = run、30 skip、0 failures/errors、无未运行套件，Root 675/675 PASS；独立候选 44 tests PASS（50.669s）。最后外层日志摘要与 final-test-summary.json 完全相同；end-validation.json 的数量/hash/结果逐项一致，末次实际重新核验全部 main/candidate raw bytes、patch 和原 authentication diff 未变。核验脚本及结果为 verify_snapshot.py / supervision-snapshot-verified.json。没有提交推送、升级 live 或访问默认用户数据库。

## 范围与实际路径

监督只读审查 Sessions 检索/生命周期、Host 原项目身份与引用包装器、Core typed request、Context 固定预算、Interfaces 用户命令及真实 Application 装配，不修改产品代码。所有新增执行测试先检查显式 TemporaryDirectory 路径归属再创建 Application/Repository；不访问默认用户数据库、不提交推送、不升级 live。

主证据从 create_application 默认 semantic 入口，通过 TUI /memory save、普通任务及实际 OpenAIResponsesClient 的 MockTransport 捕获准备后的 HTTP JSON，不手工给 builder 注入项目 ID。重启与另一非 Git 项目共享同一隔离测试数据库，验证持久项目召回及外项目拒绝。

## 审核结果

- Application 同一 ProjectMemoryControl 装配至 TUI 与 ContextAssembly.map_builder；默认 semantic 及已有上下文路径均可达。Memory 引用只进入 typed project_memory 固定前缀，不进入可信 task_facts 或权限。
- 项目身份取真实 Git common-dir 或规范化非 Git 根；Host 校验持久 lineage/冻结 authorization 与父链，未授权 taskless thread 不因 checkpoint 元数据获得身份。linked worktree 和 managed lineage 子线程有实测。
- SQL 在词法打分与分页前筛选 scope、latest revision、active、当前声明条件；连续汉字双字词元和 Unicode casefold 避免只看最新少数记录。无当前事实的条件不自动适用；user scope 默认不参与 Host 搜索。
- scoped CRUD 与修订 CAS 复用原数据库机制；删除留下既有 forget 表的正文及 ID 屏障，重开、重试、旧候选激活不能恢复被忘记条目。撤回保留审阅能力但默认召回排除。
- 来源和条件显示为用户明确保存的参考数据。发现原 show 缺少当前适用性显示后已反馈 Root；修复新增 Host scoped applicability 与 UI 当前标签，随后将独立验收。
- 引用先经最多 4 条/1024 本地估计 token/16 KiB 限额，再计入 WorkspaceContextBuilder 的 PromptBudget.allocate，最终 prepared HTTP 请求仍走已有 Guard。没有提高总 20000 或规则 6000 上限。

## 测试证据

independent_tests.py 增加真实 /new 后召回且 user scope 不泄露、110 条新噪声后旧中文召回、外项目 thread 用户命令写入前拒绝、UI needs_check/conflict 及来源展示。复用产品端到端与 Unit fixture 补齐重启、外项目、生命周期、条件、预算、Git worktree 反例；每个 Repository 均限定临时目录。

main 预验 independent-initial.log：28 tests，0 failures/errors，37.759s。该记录早于最终冻结，不能替代候选最终验收；独立脚本后续新增一条 UI 适用性测试，最终数量以候选实际日志为准。

最终候选哈希、独立复跑、标准 runner 最后外层摘要及原 authentication diff 保护结果待补充。

初次冻结候选 `72fc7fec357b2991545db1bb002b96425d9a0b5a9af25046dc91ac713d8a2dca`：29 路径 raw bytes 在 main/candidate 均匹配，原 authentication binary diff 哈希仍为 `7cbeb87002763fbe5de0bf33a4fd68193af6d66e5340be75f2a1edeb776befca`。实际 Repository/ProjectMemory 导入路径断言属于 candidate。独立实测 `independent-initial-freeze.log`：30 tests PASS，43.155s。此后 Root 发现 query fallback 物化过大工具尾的真实预算回归，将最小修复后重新冻结；初次记录保留而不冒称最终通过。

## 第二次候选复核

第二次冻结 `ee47c3a222ab53df7cca53642a507f7b0802651737537f6040bd4b479470cfb0`，32 路径 raw bytes 在 main/candidate 匹配，原 authentication diff 哈希保持上述值。实际导入模块来自 candidate。后续重新冻结记录取代其最终验收身份。

初次标准全套实际 3311 run / 30 skip / 2 failures / 0 errors。两个原回归分别暴露：外根冻结授权的 child 被可选 Memory 身份拒绝，taskless 旧路径在原规则预算检查前被 Memory 拒绝。产品修复只在投影失去此原项目身份时清空全部 Memory 参考并继续原 inner/Task 权限/预算验证，操作端 scoped CRUD 仍拒绝 foreign thread；没有改旧测试 fixture，也没有放宽项目共享。未知记录、损坏和取消错误不被隐藏。

另修复 Core user_input 为空时重复加载完整工具尾：优先已有 request.messages 的最近 user，仅不足时 role=user/newest1 有界公共读取。Host 新建 !command taskless thread 通过创建后的 callback 显式绑定；已有/恢复 thread 不被自动授权。

第二次 candidate 独立实测 independent-second.log：37 tests，0 failures/errors，49.258s。包括两条原 S7/S4 测试原方法：child 读取 FROZEN ROOT 而非 WRONG SOURCE，父任务累计 30 tokens / 1 tool / 2 turns；超规则仍为 RuleLimitError、零模型请求/零动作、文件不变。旧 fixture Repository 构造额外拦截并断言路径属于该测试 TemporaryDirectory。其余覆盖实际 !command 保存、当前适用性与来源、较旧中文、外项目和 user scope、版本/撤回/忘记/重开、大 tool 尾不重读与公有能力/预算上限。

规则真加载最终 Root 1542、Host 5922、Core 4422、Interfaces 5972、Context 5075、Sessions 5888，均在 6000 内，总上限仍 20000。最终标准 runner 完整结果和末次冻结保护核验尚待到达；当前状态 WAITING_STANDARD，不能进入 S12。

第二次全套实际 3317 run / 30 skip / 1 failure / 0 errors，Root 675 全通过。唯一 Interfaces 取消/停止事件竞争已确定定位：stop 写 FAILED 与取消结果读取并行，终态分支原来跳过绑定的 cancelled 记录并返回 TASK_RESULT，导致结果/事件依时序漂移。修复只对同 owner、明确 user stopped task 的 FAILED 记录 cancelled；其他终态维持权威结果。新增 Event 屏障强制两种持久顺序、错误 owner 及 completed/accepted_partial/superseded 迟到取消，未增加等待或跳过测试。

## 第三次最终候选终审

第三次冻结 `859ed1e22b1bd24b51661a1450e5613a86d61cc6f0010319bd97b70a4ab96abd`，34 路径 raw bytes 在 main/candidate 匹配，模块来自 candidate，原 authentication diff 不变，见 supervision-snapshot-verified.json。监督继承全部 7 条 Durable 结果测试并在每个 Repository 构造前核验 TemporaryDirectory 范围。

Context 契约旧默认 3000 注已在第三次冻结改为用户批准的 6000，与代码和 builder 条目一致。六条规则链重核都在 6000 内，总提示上限仍 20000。最终独立 44 tests PASS（50.669s），标准 30 套 3320 run / 30 skip / 0 failures/errors、无未运行，Root 675 全通过。末次保护核验已实际执行且各物证存在、内容一致；最终 PASS。
