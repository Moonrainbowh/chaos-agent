# User Interfaces
- 规则/提示预算错误只显示可信BudgetDiagnostic本地数值、相对规则路径及压缩/显式调整方法；不回显规则正文、绝对路径、任意异常正文；旧generic安全提示兼容。
同一内核的TUI、CLI/JSON；一个前台父任务，子Agent仅层级执行。仅委托注入Controller/只读服务，不直接工具/provider/Git/SQLite/文件恢复，不复制状态机、授权、索引或持久历史。Windows Terminal/PowerShell原生选择/复制/滚轮/Unicode/resize为验收基线；手机合成测试与真机验收分记。macOS端到端、Linux专有剪贴板/Win32快捷键、IDE/Web/多用户服务/桌面应用不在本Feature范围。

## 边界与强制约束

- Layout=`auto/wide/compact`，手动优先，auto≤64列用compact。wide只追加普通终端转录、刷新输入/状态尾部，保原生拖选/复制/滚轮，不加顶部固定栏、自有历史、多栏/侧栏/仪表盘。compact例外：同状态有界完整视口、固定项目/会话入口、短消息顶对齐、长消息按原滚动偏移、底部四列图标/文字触控与两行真实状态，软键盘可用；用户深绿色背景只标角色。鼠标报告仅compact，启动Picker同阈值，运行resize/layout同步切换且保草稿/附件/会话/task；过时尺寸/无效坐标/释放移动拒绝，退出关闭报告/恢复模式。不新增装饰历史动效；resize清理保持历史、单输入框与光标几何。
- 项目Picker仅显示Host目录并委托切换：运行/模态禁入，新认证/审批优先；取消保原稿，选择不同目录才请求Host重建。命令菜单保原输入对象，跨会话不恢复旧稿。会话显示标题/预览/状态/更新时间/消息数，长历史分页/窗口/搜索；历史与同机peers分开，无peer仍可看历史。`/resume`无ID开Picker；恢复task先恢复冻结runtime，终态只无prompt开历史；恢复失败不换当前会话。消息树过滤/搜索/折叠/书签/分支只改对话，不恢复文件，未配对工具处不分叉。`/清屏`只清本次转录，保thread语义；search/read thread caller身份只由Host注入。
- Enter提交；Shift+Enter/Ctrl+J换行，多行按结构移光标；待命单行胶囊、6行展开无行数标签，Space仅底部展开、看历史不改视口，Esc折叠保稿。外部`$EDITOR`/系统编辑器用安全临时文件，失败保稿，退出清理。输入统一UTF-8≤65536字节，超限整个插入拒绝不截断/不移动光标；规范换行/UTF-16字符对。单次粘贴>10逻辑行独立折叠`[chars: N]`（Unicode码点N），前后文字/其他块独立；整块编辑，发送还原原文，历史保边界但不恢复旧图片，手输同标记为文字，内部占位不发送。粘贴CR/LF/拆包标记/控制字符不得变提交；key-up/裸修饰空事件不并入burst、不重置退出窗口。
- 归一Windows KEY_EVENT/CRT与POSIX raw TTY UTF-8/CSI/SS3/CSI-u/modifyOtherKeys/bracketed paste；Windows保VT粘贴边界和Win32物理修饰，先解Win32封装再粘贴，原生Enter/Shift+Enter/Alt+V不作burst；超限排空标记，测试burst用虚拟时钟，退出/清理失败必恢复console/TTY/光标。Ctrl+C两秒双击退出：首次依状态暂停/拒绝/清空/提示，已paused单次退出；异步暂停期间仍读第二键。Esc先pausing后checkpoint，关闭中断并等待durable interrupt/checkpoint再有界等runner；显式停止失败，排队不丢；暂停仅活动runner、单pause task，不以请求当已暂停。
- 准备/模型/持久收尾/用户`!command`共单前台槽，提交即有可取消准备与持续反馈，失败保未提交输入/附件，完成回调刷新尾部；不能新增并行任务。运行Enter持久排队，普通输入Tab转向下一模型安全边界，不硬杀工具；steering消息/control由Session原子写入。`[排队]/[转向]`及数量/applied来自事件：follow-up仅TASK_FOLLOWUPS_PROMOTED，steering仅TURN_STARTED/CONTEXT_BUILT。`!`无provider委托Host并入对话，`!!`仅显示，取消保真实结果；运行保命令草稿。实际created/running才阻并发。
- model/effort同task/对话固定；成功改变空闲选择才与`/new`共用空对话重置，旧消息/task不改；同值/失败不重建，运行先暂停，旧projection epoch迟到事件隔离。行为默认auto、创建冻结ask/code；mode/topology/profile/effort与权限独立，活动/未知失败闭合，只接已配置profile ID或唯一短后缀，不接key/URL/启动命令/权限变更；回调成功才更新。显示实际model/topology/Oracle/成本延迟/effort/有效工具/生效边界；single不计隐藏delegate，Anthropic默认effort明示prompt-only/structured unsupported。权限默认auto，永久规则仅精确structured process绑定executable/完整argv/workspace identity/联网声明，raw PowerShell不得入规则。
- 单命令注册表生成parse/help/palette/completion/标签/依赖/可用性；`:`（Shift+:）开带边框/query/命令说明两列面板，兼容`/`；精确名/别名优先描述、完整参数原样；默认help/root仅19英文primary按服务启用，advanced/internal不平铺。无参数一次Enter执行，复合一次Enter入二级，Tab补全、Esc逐级回退保输入，禁用不落其他动作；`/会话`/`/权限`仅父项。Diff可完整输入advanced；`/list-agents`/`/peers`/`/rename`隐藏兼容不入注册表/root/help。`/mode`不展示菜单/推荐，旧显式兼容，无参数只报告；`/effort`独立。插件命令namespaced不可变快照、internal，撤销/禁用/digest-generation变化拒旧选择，不替换四内置mode。Picker共享过滤/补全/当前值/禁用原因/错误恢复；字符子序列+MRU，精确子串优先，支持缩写。
- `/login`仅登录；`/model`统一配置profile与已保存登录模型且成功记用户选择。WorkBuddy上次模型启动一次账号目录验证恢复，无离线种子仍可显式异步发现；目录加载失败/取消保登录/当前模型；Antigravity首层仅本地全量目录二级入口、展开不联网。自定义API模型显式Enter异步加载缓存二级目录、Esc取消、失败保当前，容量明示继承配置。凭据用独立隐藏槽：Enter确认/Esc或Ctrl+C取消/关闭释放等待；只掩码，不入普通输入历史/附件/转录，登录期间禁任务提交，错误不回显凭据。
- 有界附件草稿接显式路径、Alt+V（兼容Ctrl+V）位图/多图片文件/拖放/列表/移除；稳定`[imageN]`按光标插入、支持批量与多次、中间删除不重编号，左右跨整标记、Backspace/Delete同步移对应附件、Ctrl+U清两者、`/attach remove imageN`按号。标记不进正文，列表仅安全名称/type/size/digest，粘贴拖放不自动提交；文本+附件/仅附件/steering同主链，无输入无附件空操作。能力/摄取/提交失败保稿，仅durable MESSAGE_ADDED按本次digest移除，后续新增保留；批次预算下推、重复剪贴板digest幂等无重复提示，拒批无孤儿blob，无预算协议旧摄取器不得入新版批量UI但旧单图API保留。原生Win32空bracketed帧忽略；无原生解码器可作Ctrl+V图片手势，非空文本不误摄图片。不直接任意读取/blob存储/猜视觉能力/泄露本地绝对路径或内容。
- 默认简体中文，文件/路径/代码/命令/API/model/精确术语原文。唯一Muted Slate、无主题切换（忽略旧CHAOS_THEME），保auto/always/never颜色与显式ASCII回退；默认`›◆↳✓!×`不用Emoji/混合符号。浅色正文、品牌/选中项/结构强调色，中灰工具元数据；输入与历史用户正文同深蓝RGB30/48/76，无颜色不填背景，逐行恢复/消息后reset。默认现代单栏Markdown：标题独立行留白、粗体亮白、代码冰雾蓝，代码原样/全宽无人造缩进；表格无纵线三线表，窄屏稳定逐项。轮间一空行、连续工具无空行、工具组与结论一空行、短正文不扩张；自动强调仅确定结构/短标签不猜关键词状态，宽度/缩进/光标统一不改变输入几何。
- 增量正文在输入上方有界临时区，用revision/dirty合并安全折行与容错Markdown，未闭合标记仍可读；MODEL_STARTED新边界，完整消息清临时区并只固化一次final，成功final不截断。取消/错误/关闭固化有界PARTIAL_AGENT明确未完成/截断标签，不当final/动态尾部/会话消息/证据/完成判定；无正文总结回合可恢复错误不静默结束。原始reasoning不存/不显，仅本地生命周期或provider明确有界脱敏summary。不可信模型/工具/插件文本先去控制序列，本地可信显示事件才生成ANSI，安全Unicode/grapheme列宽；Windows路径可信OSC8打开、代码行去尾空格。
- 临时全屏蓝色大字CHAOS左到右扫描，重依赖前开始、后台帧不阻初始化/输入、不读输入/无最低播放时间，初始化完退出，恢复屏幕模式后清启动旧画面/滚动到左上；运行保新历史。重定向/help/JSON/ACP禁动画，NO_COLOR/reduced motion静态。动效只动态尾部、有界可关闭：280ms边框过渡、非百分比巡移光带、durable取消后退出≤160ms、≤30fps，空闲仅resize/过渡刷新；`:theme motion on|off`advanced、CHAOS_REDUCED_MOTION，关闭恢复默认光标。follow-up固定青色›/静态边框/细竖光标、固定三字符点阵（关闭静止），不装饰性预算进度。
- 只投影typed running/verifying/paused/waiting decision/approval/partial/completed，不把非空闲显示就绪。绿色完成标记仅任务真实充分证据，不用于Action/partial/unverified；明确缺条件、`/证据`/`/evidence`，UI不能裁决成功或依据模型自述。actual改动无验证显示验证中，错误副作用/checkpoint/下一步只据Host事实，stop reason保留；等待决定不追加冗长黄色块，完整assistant可先落屏。accept_partial仅显式用户且VERIFYING/WAITING_DECISION。计划当前完整plan/replan跨工具/轮保留，新task清、原task超时retry保；显示当前/完成步骤，Picker优先，超出查看转录。
- 用量同live/durable投影：最近request、会话输入/输出/缓存读写/命中率/已配置价格；同请求快照替换不重复累加，真实分支请求计入、复制消息不产用量，未知保持unknown/unavailable。context有效工作窗占用/编号与task累计分开、cached input不双加；`/cost`持久预算区分soft/hard/renewal不自续约，lease只typed事实、坏事件不盖旧。`/compact`真实语义checkpoint、手动换窗仅排队到下一request，不伪已压缩；`/doctor`有界真实网络诊断不硬编码。token/s首delta计时估算后按provider output校准，不含首字等待/input。状态左动态稳定、右model/耗时窄屏按优先级隐藏；durable phase累计新run清零。
- 工具request/result配对显示脱敏有界facts/目标/数量/耗时/文件预览，详情稳定ID按需，完成可折连续工具为摘要但保历史；子Agent角色/目标/耗时/有界用量及queued/running/completed/failed/cancelled来自Orchestration去重快照，不猜工具名状态，父中断正常不通用异常。runtime错误只安全有界单行HTTP/SQLite/context-rule-budget原因与恢复，不traceback/SQL/DB路径/HTML/规则正文路径/凭据，不推断未确认副作用。
- `/diff`point-in-time只读键盘modal：文件/行/区块/页/过滤/refresh/评论草稿/放弃确认/显式发送同状态，浏览键不落普通输入。Host typed写结果/只读Git生成统计与有界unified diff；评论带scope/path/稳定行锚，仅显式s经submit/steering一次发送，刷新失败保旧快照，未发评论不静默丢。计划审批Diff禁止评论/发送/刷新；preview≠apply，不运行Git/授权限/改历史。stage/unstage仅显式用户typed Git action中央策略审批审计。Rewind三模式有界preview/default No/single-flight，preview取消回收、durable execute关闭等安全完成；内部恢复仅operation ID，不接受重述rollback facts。
- `:map`独立只读语义图与context/测试影响优先级/风险/审查/重构/bug/dead-code二级动作，显示generation/置信类型/静态限制/total；limit1..50、offset0..100000、引号空格路径与`--`字面参数，不把候选当根因/安全可删。流程DAG/节点/evidence有界safe_text，无edge授权；Skill/MCP/Plugin选择只Controller、不读全文、不直接启动进程/存激活/执行proposal，未批准MCP显示禁用。notify/confirm/input/select统一Host-owned可取消，notify不等答案，其余默认不自答。

## Units
- Foreground/AgentController：统一CLI/TUI执行、持久排队/转向/暂停与冻结恢复。
- TerminalState/DisplayEntry/ExperienceSnapshot：typed事件到真实状态、facts/partial/用量的有界投影。
- WindowsTerminalApp/InputDocument/InputBuffer：单栏终端与原文/粘贴/附件编辑、生命周期模式恢复。
- CommandRegistry/Picker/ProjectPicker：单注册表、分层选择、namespaced动态贡献与项目切换请求。
- Approval/InteractionBroker：可见、默认拒绝的Host-owned审批/选择。
- Diff/Rewind models与handlers：只读预览/稳定反馈/Controller委托。
- AttachmentDraft/Auth prompt：草稿摄取预算与隐藏凭据输入边界。
- Markdown/Theme/Layout/Motion/Usage：可信样式、几何、单主题与事实计量。

## 参考
参考：`docs/next-version/s4/constraints-audit.md`、`docs/context-boundary-experiment.md`、`docs/context-boundary-results.md`（路径均相对仓库根）。历史实验不自动推广为默认策略。
