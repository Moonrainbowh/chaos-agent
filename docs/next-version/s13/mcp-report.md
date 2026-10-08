# S13 MCP Feature 自测交接

状态：SELF_CHECK；仅 MCP Feature，阶段放行由主 Agent 的完整冻结、全量回归与独立监督决定。

真实 SDK 1.29.1 红证据在 `mcp-red.log`：原版本握手由外层 4s watchdog 才收束且子进程仍活；跨 Task close 吞掉 AnyIO scope 错误且子进程仍活；服务调用后退出，health 仍 healthy=True。探针只启动显式 `s13-mcp-*` 临时根内的自有服务，清理仅核对其 PID 与精确临时目录。

修复由一个 `LifecycleOwner` Task 持有 SDK contexts 和最多 1 个排队命令，启动包含传输/初始化/发现，调用与周期 SDK ping、关闭各有期限。SDK 边界与锁定依赖未改变，服务 stderr 不回传（送空设备），只报告错误类别。`last_success` 表示最近成功协议交互，不能推断工具业务成功。未知写入不自动重连或重放，故障后须显式 restart。

重复取消启动的追加反例在 `mcp-cancel-probe.log`：SDK transport 的 `__aenter__` 未 yield 前被中断，尚未进入有界 shutdown finally，落到 Process.aclose 无限等待。修复为启动期间 stop 先标记 stopping、立即禁止新调用，等原启动期限收束，不再从外部打断 SDK enter；部分进入的 stack 由 owner 在 cleaning 阶段同 Task 按关闭期限回收。已进入调用的取消仍通知 owner，而 caller 的重复取消不会转移或打断清理所有权。取消总等待可包含启动期限和关闭预算，不承诺即时杀进程。

Controller 启用完成后才发布，发现工具必须在本地 risk 表中，且 risk 用本地值覆盖。disable 在等待取消前撤下工具/risk并改变 epoch，较晚 disable 阻止旧 enable 重新发布。snapshot/definitions/status/registry/risks 在故障时剔除失效定义，generation 变化；call 新增兼容可选 `expected_generation` 参数，批准后拒绝旧代际。diagnose 将注册服务投影 configured=True，ready 与最近协议成功、fault/error_type分开。

正式 Feature 命令：候选锁定 `.venv/Scripts/python.exe -m unittest discover -s src/code_agent/mcp/tests -p test_*.py`，PYTHONPATH 指向主源码。最终 `mcp-tests-final.log` 为27项完整结果，18.719s，失败/错误/跳过均 0，进程 exit 0，无 ResourceWarning。包括14项真实 SDK 故障/队列边界（启动失败、握手不响应、工具发现不响应、调用挂起、服务退出、闲置退出、拒绝 stdin 关闭后的收束、跨 Task 关闭、启动/调用重复取消、startup deadline pre-yield故障、排队后来源撤销/代际变化、queue-full拒绝、小关闭预算；有的单项覆盖多个行为），每个实例关闭后核对自有 PID 不再存活；调用超时核对 fixture 写入计数仅 1 次，后续调用拒绝。另有本地 risk 覆盖/旧代际拒绝、pending enable→disable及两项有限Fake watchdog失败诊断的反例。

独立监督首次发现队列时序缺口（主 Agent `supervision-queue-red.json`）：调用入队前的权限/代际检查不能保护排队等待期间的来源撤销。修复追加 `before_call` 同步回调，经 Controller→Manager→owner 保存，在真正 adapter.call 前执行，期间无 await；Controller 在相同回调组合当前可见性/expected_generation，Host 可组合冻结源指纹。`McpBeforeCallError(PermissionError)` 专门表示 SDK 尚未调用前拒绝，manager/controller只拒绝该动作，不误把健康连接标故障；取消异常不吞。两个真实 SDK 测试让首调用等待固定 release 文件、第二调用排队后撤销来源或改变代际，确认 effects只有首个，SDK保持ready且目录仍可用，第三次新合法调用正常执行。无源旧直接调用不新增必需参数。

第二轮独立监督发现队列已满的 RuntimeError 被Controller当传输故障撤Schema/risk并改变generation，从而误拒绝先前合法排队请求。现 `McpBusyError`明确SDK未调用前拒绝，保留连接/目录/风险/代际。真实SDK邻近测试让首调用挂起、第二合法请求排队，第三拒绝busy，generation/snapshot/risk均不变，再释放首调用验证首第二均完成；未增队列容量。

同轮关闭预算反例显示0.2s合法预算打断SDK shutdown finally后等待Process.aclose，可返回超时仍遗留进程。关闭watchdog现在覆盖该窗口，通过同adapter的SDK句柄收束，owner仍退出scope。fallback已耗尽原宽限，调用官方termination helper的timeout_seconds=0立即升级，不额外添加其POSIX默认2s宽限。复制监督实际脚本复跑证据在 `mcp-close-budget-probe.json`：close0.2、0.215804s返回、owned_child_alive=false、probe_cleanup_required=false；原监督红未覆盖。Windows真实验证成立，Linux/macOS目标平台仍由主Agent/CI单独验收，不冒称本机验证跨平台。

有限Fake独立反例显示watchdog except TimeoutError分支内部的termination helper异常未被观察。现共享 terminate_on_deadline 捕获helper的普通异常，存cleanup_error；health的error_type优先投影实际回收失败类别，fault=True，而ready独立False。两项标准发现测试逐一验证startup/close helper RuntimeError，close后error_type仍RuntimeError、faultTrue且loop exception handler无未取后台异常；复制独立脚本 `mcp-watchdog-error-probe.json` 同样记录 unobserved_watchdog_errors=[]。未把此Fake当真实SDK清理物证，SDK小预算证据另列。

Startup deadline 本身的 pre-yield 窗口不能仅靠延迟 caller cancel解决。正式测试在真实官方SDK Process.__aenter__（真实child已spawn、stdio尚未yield）注入阻塞，0.5s启动期限触发取消后 Process.aclose 等待不退出；3s关闭预算后watchdog通过本 adapter持有的 stdio context generator 局部process句柄，调用锁定SDK自带 `_terminate_process_tree`，同时关闭该context的4个memory streams（失败entry会跳过 SDK finally）。SDK contexts仍由原owner退出；不枚举机器进程，不自写协议。测试明确marker已达且总退出<7s，并验证PID已消失。此为SDK1.29.1的私有生命周期兼容点，未来更换SDK须重跑该故障用例；当前SDK版本已实际核验1.29.1。

追加取消测试的第一版等待 PID marker 未设 watchdog，暴露该 SDK 窗口后被中断；随后 marker 先于服务重依赖导入，并有截止/Task完成条件，最终20项完整通过。最早 green 探针碰到 PID 已消失的 psutil 查询竞态，正式测试用精确归属及进程 wait 处理，未把该探针当成通过证据。

`mcp-rule-probe.py` 实际生产规则加载及具体token值在 `mcp-rules.log`，低于6000，总提示上限20000；未构造 Session DB。`mcp-files.json` 精确列出8个产品/测试路径及 raw SHA256（新文件也包含）。所有新模块低于300行，未改其他 Feature、Host、候选 checkout、真实配置、用户数据库、原8787 Host、依赖或 Git提交。完整阶段/平台验证尚由主 Agent执行。

兼容：McpHealth 原 server/healthy/error_type字段保留并新增字段；Controller.call 的新参数默认 None保持直接调用兼容；相同 ready 连接重复 start 使用已发现工具，显式 restart 重新握手/发现。无数据库迁移；回退只恢复这8个路径对应的阶段前字节，不恢复用户其他改动。独立监督应特别重跑 SDK 进入阶段取消及关闭、启用/禁用竞态、故障目录撤销以及 Host 已批准调用的 generation 核验。
