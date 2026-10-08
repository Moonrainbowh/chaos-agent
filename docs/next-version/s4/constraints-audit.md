# S4 强制约束与压缩设计审计

本文件是当前 S4 的只读审计产物，不是规则压缩实施或独立终审；除本文件外未修改产品代码或规则文档。结论：两份 Feature 文件存在大量接口罗列与重复，可显著压缩；但不能把 Units 整段视为参考材料。若约 2000 token 指每条实际加载链（共享 root + Python + Feature），尚无充分依据承诺完整 Interfaces UI 契约能容纳；预算应由完整改写后实测决定，不能删除用户要求换取达标。约 2000 若只指 Feature，可作为尝试目标，不作强制截断线。

## 审查输入与计量

逐行阅读 `src/code_agent/interfaces/AGENTS.md` 全180行、`chaos_agent/AGENTS.md` 全90行，并阅读共享 root/Python 规范。使用项目 `code_agent.context.tokens.estimate_tokens` 实际计算；这是保守本地估算，不是 Provider usage。该算法 ASCII 每4字符约1 token，非ASCII UTF-8每3字节约1 token，中文不能按英文每4字符粗估。

| 文件 | 本地估算 | SHA256 |
| --- | ---: | --- |
| interfaces/AGENTS.md | 14163 | 68858866cec007d2bc1f72f264c4459b01be288b7ea001123f147f8dc235d16a |
| chaos_agent/AGENTS.md | 8727 | ea4a927451c95f3da13b6fc371f545cbc67f77354ca16d1cb06afefe42d0e6f3 |

共享文件由主 Agent 在 S4 中维护，审计时量得 root 1896、Python 1143；不是压缩后的最终预算，也不能与后续新文本混为同一快照。未运行 RuleLoader/真实Context，本报告只提供内容审计，不证明默认上下文通过。

## Interfaces 必须保留的约束类别

以下 I 行号指上述 Interfaces 审查快照。每行是一组必须保留的语义；允许合并措辞，不允许移入不默认加载的参考文档后只留一个链接。

| 类别 | 必须保留的行为/边界 | 依据 |
| --- | --- | --- |
| UI/Host 权责 | 单内核单前台父任务；命令、审批、附件、Diff、Rewind、扩展仅委托注入Controller/只读服务；不直接工具/provider/Git/SQLite，不复制状态机/索引，不从mode/peer/workflow/plugin推导授权或证据 | I38,49–54,60–78,83–84,129,134,158,170–171 |
| 授权交互 | 审批可见、默认拒绝、一次性明确选择且可取消；真实动作/风险/目标/原因；插件不得自答或生成终端控制码；计划Diff只读，不评论/刷新/发送 | I61–62,134,150–151,156–157,167 |
| 持久任务控制 | Enter运行中持久排队，Tab转向下一安全边界；Esc先pausing后checkpoint；关闭等待interrupt/checkpoint持久收尾，显式停止失败；排队不因暂停/关闭丢失，事件决定applied，不以请求中冒充已完成 | I55–59,129–130,149,154–155 |
| 运行时冻结 | 模型/effort同task固定；空闲成功改变才新会话，同值/失败保留旧记录；运行中先暂停；resume恢复冻结runtime，终态仅开历史；恢复失败保留当前会话，旧epoch迟到事件隔离；权限与mode独立 | I15,25,73,113–114,161–166 |
| 项目/会话 | 运行中或模态禁切项目；取消保留草稿，认证/审批优先；会话/分支只改对话不恢复文件，未配对工具位置不分叉；历史分页/搜索，caller身份只由Host注入 | I6,9,63,72,92–95,100,164 |
| 布局与终端归属 | wide追加式转录、原生拖选复制滚轮，只动态尾部；compact有界同状态视口/项目栏/底部触控，顶对齐、四列控件、绿色角色背景；auto≤64列compact，手动优先；鼠标只compact，退出恢复，尺寸/布局切换保留身份/草稿/附件，旧点击拒绝 | I5–7,26–28,53,76,94–97,110 |
| 输入协议 | Enter提交，Shift+Enter/Ctrl+J换行；Space仅底部展开，Esc折叠保草稿；6行工作台无行数标签；安全外部编辑器失败保稿；粘贴不提交/控制字符不泄漏，退出恢复TTY/console | I12–13,29–31,112,115–120,139–140,177 |
| 输入容量与原文 | UTF-8 65536字节，超限整个插入拒绝不截断/不移动光标；>10逻辑行粘贴独立[chars:N]按Unicode码点折叠，发送还原，边界保留，手输标记仍普通文字 | I13,116–117,120 |
| 附件 | 有界草稿，Alt+V兼容Ctrl+V/拖放/仅附件/steering同主链；稳定imageN编号不重排，原子标记删除、Ctrl+U同步清空；标记不进正文，仅durable消息按本次digest清稿，失败/后续新附件保留；不泄露路径/blob，不猜视觉能力，批次预算下推、digest幂等、不产孤儿blob，非空文本不误摄取图片 | I79–81,116–117,121,160 |
| 语言与主题 | 默认简体中文，路径/代码/API/专业术语保原文；唯一Muted Slate、不提供主题切换，保NO_COLOR/reduced-motion/ASCII；Unicode符号不用Emoji/混合语法；用户蓝底具体RGB30/48/76、compact用户深绿只表角色；浅色正文、亮白粗体、冰雾蓝代码、绿色Diff，不按关键词猜状态 | I12,15–18,33–36,111,123–126,180 |
| Markdown/转录 | 流式正文有界临时区，容错标记、本地可信ANSI，最终消息只固化一次；取消/错误明确未完成，草稿不是消息/证据；原文代码不变，无原始reasoning，仅明确安全summary；三线表窄屏逐项，中等留白，单栏不添仪表盘；清屏保thread | I20–23,34–37,45,74,76,108,111,141–145 |
| 动效与启动 | 重依赖前临时全屏蓝色CHAOS左到右扫描，初始化完成即退出无最低播放时间；启动清旧画面/滚动，运行保新历史；重定向/help/JSON/ACP禁动画；不阻塞输入或持久取消，最高30fps，280ms过渡、退出至多160ms；follow-up青色›/静态边框/细竖光标/固定三字符点阵，关闭恢复光标 | I11,17–18,109,125–126,149,180 |
| 命令目录 | 单注册表解析/help/palette/补全/可用性；冒号主入口兼容斜杠，19英文primary按服务可用，advanced/internal不进默认根；无参数一次Enter执行/复合进入二级，会话与权限根不平铺；/mode隐藏且无推荐，旧无参数仅报告；/login仅登录、/model选择、/effort思考深度；peer隐藏兼容不污染目录，插件namespaced/digest变化拒旧选择 | I38–44,132,135–138 |
| 模型目录/认证 | 隐藏凭据槽不入历史/草稿/附件/转录，登录阻任务提交；Enter确认/Esc或Ctrl+C取消；模型显式异步发现可取消，失败保当前/登录；WorkBuddy一次目录验证恢复，Antigravity首层本地目录二级入口、不联网混入首层，容量明示继承配置 | I43,101,106–107,161 |
| 状态/证据/用量 | typed running/verifying/paused/waiting/approval/partial/completed，非空闲不冒充就绪；绿色任务完成仅真实充分证据，工具完成/部分/未验证禁用；副作用/checkpoint不凭错误推断；缓存未知/价格未知保持未知，同请求快照替换不重复累计，branch复制消息不产费用；context占用与task累计分开，lease不自续约，换窗仅排队；token/s从首delta计时并由真实usage校准 | I8,34,46–50,58,87–89,98,102–103,124,127–128,141,147,173–175 |
| Diff/静态报告 | point-in-time只读modal，按键不落普通输入；显式发送一次带scope/path/稳定行锚；刷新失败保旧快照，未发评论不丢；stage/unstage只能显式typed action策略审批审计；map显示generation/静态限制，候选不是根因或可删结论；limit1..50、offset0..100000、引号空格路径/--字面参数 | I47,52,67–71,146,158–159 |
| 子任务/错误 | 子Agent queued/running/completed/failed/cancelled来自Orchestration快照，父中断正常；有界目标/耗时/用量，不猜工具名状态；不可信文本清ANSI、诊断不回显凭据/SQL/HTML/库路径/规则正文，不直接traceback；失败保恢复动作，不伪造事实 | I37,64–66,103,141,148,168–170,178 |

Ctrl+C不可只写“二次退出”：两秒窗口；首次按状态暂停/拒绝/清空/提示，已暂停单次直接退出，空KEY事件不重置窗口，暂停异步期间仍能读取第二次，退出必须恢复console模式（I32,112,139,155）。精确process永久规则只接structured process，绑定executable/完整argv/workspace identity/联网声明，raw PowerShell不进入永久规则（I166）。

## Host 必须保留的约束类别

以下 H 行号指 chaos_agent 审查快照。

| 类别 | 必须保留 | 依据 |
| --- | --- | --- |
| 工具/中央权限 | 先按mode/role过滤再compact；原名兼容限已披露operation；监督纯Host解析不等于授权；qualified插件执行保双层风险策略，MCP opaque；schema/参数校验在副作用前，Web启用不授网络权；single/team/child委派协调边界与rename禁暴露 | H5–6,19,34–37,66–68 |
| Shell/读工具 | 冻结PowerShell/POSIX方言不自动翻译；非零/故障真实失败；structured process无shell/env/stdin，拒launcher/.cmd/.bat/NUL/超限；逐流严格解码，截断和Base64/codepage真实；slice generation/签名前后核验任一stale不返回部分源码，1–16 targets、device64/file128位不截断，auto文件不猜legacy编码；list默认25/max50/next_cursor | H10,15,34–39 |
| workspace隔离/用户数据 | source默认auto/direct，不无因探测Git/dirty/复制；并发写者/background/explicit才隔离，direct与managed各遵其语义，失败不静默回source、未知mode拒启动；敏感路径独立opt-in所有Guard共享；storage中间根不能workspace；短新根仅写新snapshot，旧missing-only只读fallback，不GC/迁移/回填旧根；禁止自动Git初始化/提交、daemon | H38,51–56 |
| lineage/recovery/清理 | 同source单写者，无隔离能力先于thread/task拒绝；失败补偿/可恢复interrupted；本地lineage不复制文件、不创建目录分支，无法取得事实metadata-only而其他SQLite故障不吞；回收仅证明无人任务/无pending rewind/无未终态/无保护snapshot，注册worktree/branch/ancestor/tip/clean均核验失败保留 | H52,55–59 |
| mutation/恢复 | canonical root分别复用gate/editor/snapshot/plan，source/task不混、Sessions同一个；PRE/POST首次写前落盘、未知写者gap；owned-only恢复，foreign conflict阻后写；批次先rewind，加锁后重读不缓存/不跳conflicted，失败关闭；已不存在task root可跳，source缺失不可吞；plan不可变/IDdigest/一次消费/apply重预检/ignored-untracked风险/模型不得自报可信风险Diff | H72–78 |
| runtime与模型 | 主/子/切换共享冻结方言/capability策略；活动不可切，原子替换失败保旧；恢复model/protocol/host/digest漂移关闭，完整ModeSnapshot不能降到宽内置mode；profiles明确存在、未知拒；Anthropic medium prompt-only、不猜wire字段，非默认effort拒；partial client任意BaseException回收且cleanup不盖原错 | H43–45,47,60–65 |
| 会话/身份/凭据 | 项目切换明确root不依赖cd，历史/Picker不调模型；branch不恢复workspace，不复制task/budget；终态续聊恢复冻结model、禁跨项目；API发现继承protocol/auth/capacity/modality/budget，不切默认/写配置，失败保旧；登录隐藏key原子保存结果，刷新失败不伪报登录失败、不触发推理 | H20–29 |
| 预算/证据/共享服务 | SQLite真实facts、unknown usage保预留，离线不是API成绩；project/user记忆Host可信身份且默认关闭不猜目录；共享RepoIndex同代query/graph，不另建索引；verification默认关闭仅CHAOS_STRUCTURED_VERIFICATION=1启用，关闭隐藏拒工具但变更跟踪保留，非修改不调无关验证；预热关闭等待，close幂等不删除持久状态 | H7–8,26–27,31,33,42,44,69–71,80–81,86,88–90 |
| CLI/扩展/审计 | help/version先于Application，ACP stdout仅JSON-RPC且无位置附件，不启TUI审批；Ctrl+C130无traceback；isolated/reclaim显式且重复拒，reclaim不与位置同用；metrics不存参数/输出/path正文，trace可关闭；peer不构成授权/task-owned不后台恢复，taskless只list/send | H40–41,46,48–50,83–84 |

## 可合并、迁出与不能误迁出的内容

- 可迁到非默认参考：函数签名/逐Unit调用表、实现拆分说明、颜色/终端预览命令示例、CSV continuity实验分组与历史日期/报告链接、启动配置示例、测试用例/日志位置。CSV实验20轮/100工具等不是生产默认值；可保存在评测契约docs，不能把限定预算/unknown usage原则一并删掉。
- 可合并：I边界与Units大量重复（输入/粘贴/附件/布局/Markdown/审批/任务冻结）；H边界与装配Units重复（工具授权、共享索引、worktree source、恢复）；root阶段规范与模板/示例/重复执行守则可压成阶段权限表+真实例外；Python命名/粒度/检查命令仅保一份。
- Units不等于参考：I134/139/149/160/166、H52/57/63/72–76中的安全条件、取消顺序、数字与失败分支必须抽取至默认规则。接口名字可以减少，条件不能只留在外链。
- 每个Feature保留少数主要接口职责和协作关系；逐函数参数/返回值放代码docstring，历史完整快照可归档但不得标为活动强制规则。代码能查到某数值不等于用户要求可以从默认规则删除。

## 数值和策略保留清单

Interfaces：auto≤64列；6行展开、65536 UTF-8字节整次拒绝；粘贴>10逻辑行/Unicode码点N；双Ctrl+C两秒且已paused单次退出；唯一Slate与RGB30/48/76；四列触控；19 primary菜单边界；280ms/≤160ms/≤30fps/固定三字符点阵；map limit1..50/offset0..100000；三种Rewind/default No；稳定imageN不重编号。保留Enter/Shift+Enter/Ctrl+J/Tab/Esc/Alt+V兼容Ctrl+V语义；不能拿模糊“保留既有交互”替代。

Host：slice1–16、device64/file128；list25/50；直属marker枚举≤512且不递归；Ctrl+C退出130；structured verification默认关闭与显式环境开关；memory默认关闭；topology权限边界与完整冻结digest；managed/source策略/敏感opt-in/legacy只读fallback/恢复顺序/cleanup闸门。四档mode实际绑定不是S4删角色机会；child角色对应profile路由可移接口说明，但冻结profile/预算/权限契约保留。Python300行目标、50行拆分信号是软信号，不改写为强制删行；600秒suite timeout/2秒诊断宽限/124退出是runner技术契约，可放测试参考但默认规范须保实际验收命令、超时不算通过、全量发现/继续汇总与清理失败终止原则。

## 需要显式处理的现有不一致

1. I43要求/model/effort、隐藏/mode且无推荐；I122仍写/mode默认推荐auto。不要通过全文迁走来掩盖冲突；压缩应以明确用户要求为准，历史菜单说明作为参考，不在S4改产品菜单。
2. I12/111要求现代单栏标题独立成行，I108仍记左列章节编号布局。I27/53限制wide仅尾部，I144又记resize全屏重绘。应分别保用户显示要求与resize清理不破坏历史/草稿的保证，把实现历史作为可追溯参考；有证据缺口列入台账，不暗自宣称两个行为完全一致。
3. I11启动清旧滚动，与I109恢复原屏幕应区分临时alternate screen退出和随后启动清理；不是运行期间可清历史的授权。

## 约2000 token的现实判断

不能把两Feature的总和误作一条链；也不能把root/Python计数漏掉。该任务给的是两个巨大单Feature输入，实际RuleLoader继承/src层与系统提示是否另有规则仍须主Agent实测。

对Host，合并逐Unit签名、评测说明和重复边界后，接近2000的单Feature草案值得尝试；完整加载链还需共享规范空间。对Interfaces，在保留上述18类全部用户UI要求、附件持久协议及数值后，约2000的整链预算过于紧，不能作预先保证。建议先完整压缩、逐条映射后量实文本；若仍超限，允许约束完整的较大Feature与受总context上限约束的显式预算。这里不是证明某个最短文本的数学下界，也没有执行压缩后的token测量；不可把本判断写为“已证明无法压缩”。

尤其不能把必要UI约束换成“详见docs”当作缩小默认token的技巧。可迁的是说明和历史，默认文本仍须表达权限/工作区/用户UI强制条件。最终验收应逐类别/具体数字核对，并运行代表目录真实Context以及人为超限零副作用反例；本报告不替代该监督。

## 压缩候选交付（非正式规则）

已按补充授权创建 `interfaces-proposed.md` 与 `host-proposed.md`，未改正式AGENTS或产品代码。以明确边界为准、抽取Units内真正限制并合并；8个主要Unit组保留协作定位，逐函数签名/重复职责不再全列。不是用模型摘要替代规则：这是可逐条审查的直接文本改写。

项目estimate_tokens实测：Interfaces候选4265（原14163，约下降69.9%）；Host候选3439（原8727，约下降60.6%）。这两个数均为单Feature，root/Python/其它祖先规则/系统提示尚需另外累加，不能声称约2000整链已满足。所有数值限制、审批/身份/预算/恢复条件未因token目标降级。

已发现冲突的选择依据：

- `/mode`使用I43明确边界：隐藏、无推荐、旧兼容无参数仅报告，删I122旧默认推荐。没有实现菜单改动。
- 正文排版使用I12/I111现代单栏、标题独立行、原文代码，I108的左列强编号作为旧接口描述不进入候选；不把原消息改写为新编号。
- wide使用I27/I53/I76不破坏追加历史、仅尾部动态更新；resize保留必要统一清理/单输入框/草稿光标保证，不以I144旧全屏重绘描述赋予常态清历史权限。
- splash先退出临时屏幕恢复模式、再启动清旧历史，运行保新历史；不把恢复屏幕与启动清理混为同一阶段。

Host候选保H62 runtime拆分Unit≤300行的专门约束；不能用Python一般“300行目标”把它弱化。候选保CSV实际证据/unknown usage/固定设置/完整tool组界限，但20轮/100工具只明确归属评测；未将其变成生产默认。历史上下文实验已有可解析docs链接；完整旧规则归档/迁移链接由主Agent在实际迁移时生成并按新位置修正，本子任务不伪称归档已生成。

本候选尚未经过逐条独立监督、RuleLoader/context组合测量或人为超限测试，不是S4 PASS。建议主Agent先审查完整性再替换正式规则；不通过机械删用户UI细节来逼近2000。
