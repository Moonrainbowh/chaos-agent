# S3 紧凑工具监督等价

状态：DONE。S2 与本阶段均由独立监督放行，可进入 S4。

起点为候选 d4f8fe8 与主工作区保留的用户 authentication 差异。S3 单独14文件 diff 和逐文件哈希见 implementation.patch / snapshot.json；未提交或推送 S3。隔离候选工作树也同步这14文件供审查，基线提交不变。

## 实际变更

- Core 实现：宿主纯解析产生观察调用，保留 call ID；共用已知底层操作的只读/进展分类。精确读重复、动作熔断、重复失败、回合观察都读取解析身份，真实执行、结果和消息仍保留模型调用名。
- 分类：read_file/read_code_slices/search_text/list_files/git_status/git_diff 为只读；write_file/replace_text/apply/verify/new_context 沿用进展动作；plan、command/process 与未知扩展不新增进展豁免。不同 execute 子操作不合并为同一身份。
- 原重复失败记录只接受 dict，但实际事件冻结为 Mapping；改为接受 Mapping，使宿主失败记录实际生效。既有熔断测试 Fake 返回的 request_id 原为 result，与请求不配对，过去隐藏了异常；仅修 fixture 为真实 call ID，保留全部第三次阻断和配对断言。
- 集成：RestrictedDispatcher 先还原 compact 再委托 Host 观察解析；Root 与默认 TaskScopedDispatcher 复用纯插件目标解析，不建立工作区服务。插件实际执行仍是 qualified 名称，原有插件/目标双层策略保持；MCP 保持原 namespace 与参数，未知操作不从模型参数或自报风险获得进展类别。
- 新测试从 create_application 的默认装配取得真实 Engine/Dispatcher/SQLite/mutation；仅模型与上下文换为离线脚本。观察器使用 spy 包装真实实现，核验实际传入的对象、物理文件和配对消息。

## 自测

- core-tests.log：最新161项运行，0失败/错误。包含解析身份、错误解析、changed ID、跨别名熔断、冻结嵌套参数/结果以及既有 Engine 测试。
- default-assembly-tests.log：3 项真实默认装配回归通过。compact/legacy 连续5写+5替换全部实际成功、没有提前停；修改后重读按新内容重新计数，真正重复读达到2警告/3暂停；失败后换别名两个后续调用被拒绝，仅第一次有 ACTION_STARTED，3个原调用 ID 的消息完整配对。
- extension-tests.log：2 项通过；execute五子操作分别解析，Host插件观察别名不替换实际qualified dispatch；MCP opaque身份不视为progress。
- root-integration.log：嵌套冻结JSON修复前，锁定依赖 CPython3.13.2 的根集成全套585发现/运行、0跳过/失败/错误、exit0通过。使用 `scripts/run_test_suite.py --start-dir tests`。最终修复后的默认装配全6项复跑通过，见 default-final-tests.log；没有声称最新树根全套586项已运行。
- nested-read-before.log：新增切片回归实际复现 MappingProxy JSON 序列化异常。观察器改用现有 plain 还原冻结嵌套JSON，保留原字段忽略和计数策略；最新默认装配验证3次 generation-bound 切片读取均真实成功，触发2警告/3暂停，原消息ID完整配对。default-slices-test.log保留一次测试错误：切片API本来归一行尾，预期修为 same\\n，不改产品输出。
- core-initial-failure.log 保留修正配对 fixture 前的一次失败；旧扩展测试的错误字段与默认 TaskScoped 缺少观察透传在当前阶段已定位修正，没有把失败运行写成通过。

没有调用真实 Provider，没有放宽权限或增加等待阈值；S9 的正文清零、真实进展与验证策略未改。S3 只把原策略接到相同底层身份。回退本阶段14文件即可恢复 S2；用户 authentication 和数据库不在回退范围。

## 独立监督

独立监督 PASS，审查 patch SHA256 `d968a7df605566455a7f10ba37a53e0c25ee5a105262cd45c4f218df352d9290`。监督者亲跑默认装配6项、Core161项和插件/mutation/slice12项全部通过；自建TEMP mixed compact/legacy普通读及切片反例均观察到[2,3]。旧切片漏检先实际复现、最新同一反例通过。14文件哈希吻合，diff检查通过，未发现阻断问题；明确585根全套属于嵌套修复前证据。未提交或推送S3。
