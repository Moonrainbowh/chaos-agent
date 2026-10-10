# 固定任务与基线比较

没有在同一模型、参数、环境和预算下完成S1/最终候选配对真实任务，因此不报告成功率提升、提速、节省token或人工介入减少。当前GLM5.3-flash/medium五CLI样本及两个真实隔离VSCode ACP样本已有Provider实际用量和端到端耗时，见budget-300k-retry.md及vscode-acp-acceptance.md；失败尝试单独保留。首个有效反馈与资源峰值未专门测量，不从总耗时推断。

可直接复核的变化：S1 runner列29套，遗漏remote与模块式Memory测试，不能称旧回归完整通过；S2补为30套并在其冻结版本完成五矩阵CI。最终S15仍30套，3440发现=执行，30skip，零失败错误遗漏。这是覆盖与发现证据，不是同一固定任务集的性能比较。每阶段失败、修正和跳过保留，见阶段原报告。

同环境S14/S15包对照：正确候选来源baseline runtime 1,244,751bytes、675成员；新runtime 1,159,789bytes、637成员，压缩减少84,962bytes约6.8%，开发包单独71,379bytes。仅产品wheel体积，未把依赖体积算入，未推断内存或启动加速。

离线基准v1/v2检查确定性轨迹与反例；实际Host A8轮24工具的public/hidden/fresh检查通过，但没有调用真实模型。A首窗是稳定虚拟窗，真实SQLite committed_windows=0；统计标window_metrics_version=2，历史结果未重算，不能与旧错误计数直接合并。

最终真实对照必须保存任务、模型/profile、effort、工具授权、预算、环境、每次失败和Provider实际usage。基线若不能在同样条件安全运行，则标明无法比较，不补造改善数字。

历史463dc980源码包1662文件逐raw等Git tree；仅测试改动后复用534b的实际wheel：runtime638成员、1,152,347bytes，development45成员、70,877bytes。新wheel的所有内部文件与已完成两版干净安装的4ed wheel相同，独立核验见final-review-provenance.json。压缩容器hash不同不代表产品内容不同；这不是整体性能度量。

配置核验更正：此前 CLI/ACP wrapper 写入 timeout_seconds=90/max_retries=0，但 loader 不支持这些字段。真实 Provider 使用60秒/2重试默认值；没有配对旧版实验，不以未生效参数宣称等限额对照。既有完成任务、实际 usage 与 SYSTEM 回执仍按原范围保存；实际重试次数未由每个旧样例线缆审计证明，不能仅由逻辑 request ID 计数推断零重试。

真实调查单独记录，不能合并为成功样本：v1 157.660秒、6settled请求、input51707/output1545，24工具组门暂停。v2 284.432秒、11审计HTTP、10settled+1pending，known input93040/output1752、pending reservation80703；SYSTEM因期限取消，不能报告完整usage。v2真正手动换窗/笔记和历史恢复成立，但只读child首次请求需要33407预留>30000局部额度，先于HTTP被拒。offline原生产复现HTTP0精确确认，不代表真实child通过；没有总体完成率或性能提升结论。

最终afbd候选：五平台各3482发现=执行/30套，Windows17skip、其余199skip，零失败错误；136根模块/732测试ID。新源码1665文件，runtime1,153,202bytes/638members，development70,877bytes/45members。相对历史产物差异包括真实生产修复和测试，不能解释为性能改善。

v4真实110.622秒/5HTTP全部settled28514input/890output；public schema100k拒绝300k委派，无child，保留失败。v5真实192.078秒/5HTTP全部settled27992input/1118output；其中child3003/141，工具5>4整组拒绝，零child工具执行；父读全四源但引用行号偏1。后续v6只修必需披露额度和明确1-based物理行号标准，旧失败不重标。

旧b1aad CI37576490916 Windows313真实2errors（ACP取消测试TimeoutError与Temp SQLite占用清理），其余四作业success。日志中另一次failures1来自group runner自身负例，不并入产品失败；最新afbd完整CI同模块通过，Root另补19tests实际0，不声称预算修复解决了上述旧错误。

v6真实284.334秒/11HTTP，9settled/2pending，56956input/1476output只是已知下界；child39232+parent56354=95586预留未知。Child五工具与四源原文真实执行，前三模型阶段49.143s、21.701s、第三9.126s后触及active90取消；父280秒watchdog（300秒外层）取消。没有额外HTTP retry证据，不归因于实际未观察到的重试。父还存在无generation/伪造0signature的slice调用拒绝与重复读取；增加期限不能保证来源质量。下一v7同源/模型/1m300k/12round40tools，child240/外层900独立样例；明确小文本使用原文read、无真实manifest不得编造切片身份，不提供答案或金标准。当前v6不能视为已完成。

v7真实212.510秒/6HTTP，6唯一usage全部settled33577input/1292output（含child6537/121一次），本样例pending0，不冲销历史unknown。新240/900期限未触发；child只load contract后给未来计划就结束，0source read；父最后actualwire四原文toolbody完整覆盖，child实际wire无四来源，不能报源覆盖或child目标成功。父实际误判list(values)保序/保dup且测试refs偏1。独立v7-actual与wire-source审查标PARTIAL/FAIL，证明扩大额度不能使模型质量自动通过，不无差别重跑同配置造PASS。
