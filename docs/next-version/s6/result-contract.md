# S6 结果与退出语义

共享 `TaskResult` 是现有任务/事件的有界投影，不另存权威状态库。version=1，字段为 execution_status、changes、verification_status、remaining、stop_code、stop_reason。执行完成与验证通过分开：旧完成事件仅证明执行完成，验证未知；正文、普通命令退出0和子任务建议不产生验证证据。

Core 使用真实 completion assessment 填验证与剩余项，发布前持久化含result的事件。Foreground 查询复核 task 更新时间、generation、subject_hash，历史结果不匹配则重新投影当前状态并保持验证未知；不能解析stop_reason冒认通过。取消是本轮run结果，不抢写pause/stop/quiesce的任务状态；取消投影绑定最近的持久running run_instance_id及generation/subject，后续恢复的新run标识使旧取消失效，暂停/停止收尾的更新时间变化不丢失取消事实。CLI流结束后优先读取持久事实，读取失败明确unknown/state_read_failed。

`run --json [--require-verified] <prompt>` 最后增加 kind=task_result 的JSON事件；保留旧事件名和字段。默认completed（包括显式未验证交付）0；failed1；初始化/usage2；waiting_decision3；paused/interrupted/unknown4；accepted_partial或要求验证未获verified5；cancelled130。`task result <task-id>` 成功查询JSON返回0，不因被查询任务失败改变查询退出语义。

两个child runner共同收集明确终态，保留原用量、thread引用和有界摘要；空流/正文流不判completed，取消不因后来完成事件升级。缺终态可查询当前持久任务；无可确认事实保持interrupted/unknown。子结果一直advisory，不转成父验证证据。

远程错误事件为诊断投影，最终始终核对durable任务，不因临时task_failed跳过；tool_finished保留真正is_error。完成、等待、暂停和最终task_status携带结果；手机显示验证结论，终端ExperienceSnapshot读取同一投影，未验证结果不获得已验证完成标记。

兼容与回退：新读取器可读旧事件与任务，缺验证元数据保持unknown；原TaskStatus及数据库schema未改。新增持久TASK_RESULT事件不是旧S5枚举的一部分，不能宣称旧二进制能直接读取已产生S6事件的数据库。回退代码时保留该数据库及S6读取器，或使用执行前数据库副本；不删除事件、不改写历史。当前验证使用临时数据库，未升级正在运行的本机Host。

候选锁环境全量与实际CLI子进程已通过，尚待独立终审；未推进S7。
