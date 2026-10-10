# S14 真实路径成本测量

本报告仅成本诊断，不是产品性能保证或简化方案的通过证明。执行脚本与逐项原始事实见同目录 `cost-probe.py`、`cost-probe.json`、`cost-probe.log`。

## 样本与边界

- Windows，AMD Ryzen 9 9950X，16核/32逻辑CPU；candidate锁定Python 3.13.2，显式主工作区PYTHONPATH。只用本次 `TemporaryDirectory(prefix="s14-cost-")` 下自己的 Git 仓库、Session SQLite、产品状态与 snapshot store。
- 每种规模单次样本，300和1500 tracked文件，每文件约1 KiB，其中20%初始内容相同；另含1个非忽略未跟踪文件，1个tracked文件在commit后处于dirty状态。5项后续编辑均为fixture自有文件。
- 真实 `create_application` 组合后执行公开 `foreground_tasks.start`、`checkpoint_control.create`、`preview_rewind`、`execute_rewind(confirmed=True)`；读取用真实 `ContentAddressedSnapshotStore.materialize`。模型工厂仅注入不支持推理的object，占位但不调用Provider；无Fake成本/存储实现。
- 这里没有调用 `Application.startup`，因此不包含Host启动对账和后台RepoIndex预热成本；脚本虽检查并等待已有warmup task，本次task为None。Direct启动计时不含Application构造、fixture生成和Git初始化/commit。磁盘缓存已因fixture写入/Git操作温热，未清理OS缓存。原始JSON中warmup描述应按此实际边界理解。
- wall为 `perf_counter`，CPU为Python进程 `process_time`（含其线程，不含Git子进程CPU）；磁盘为文件逻辑bytes/count，不含NTFS分配、目录开销。product增量包括SQLite及其journals，blob增量独立统计；不能将二者相加后当作数据库压缩率。
- Git初始化和commit只发生在自有fixture，用单次 `git -c user.name/email`，不修改真实仓库或用户Git配置。所有状态目录显式注入；Session数据库绝对路径在Temp内断言；trace关闭。

## 判读

Direct创建任务需要持久化任务/预算/lineage/元数据，但不做全量WorkspaceInventory或WorkspaceEditor.snapshot；两项真实调用计数及SQLite workspace_snapshots行数均直接验证。不能据此宣称整个Host启动没有文件扫描，后台索引不在本计时范围。

CAS同内容重复快照不新增blob；每个新checkpoint仍需要枚举、读取/校验内容并持久化独立清单及游标，所以时间和SQLite元数据仍增长。5项内容更改仅增加5个对应blob，避免按全量文件字节重复复制。

确认代码Rewind真实完成并恢复dirty和untracked基线，覆盖了此fixture自行制造的5项后续编辑。该测量仅在明确确认的自有fixture成立，不泛化为可撤销任意shell、外部服务、未知编辑或任意用户文件；并发foreign identity、预览后的漂移和崩溃恢复由其他故障物证单独裁决。

成本数据支持保留Direct metadata-only启动及CAS内容去重。没有证据支持删除身份、journal、预览复验或Sessions元数据，也不支持为这组单次温缓存样本扩建另一套snapshot/cache机制。

## 结果

最终完整复跑两组断言通过，自有 Temp 根已移除（本轮再次 Test-Path 为 False）。首轮结束时 SQLite 连接未关闭导致 WinError32，保留 cost-probe-first-cleanup-error.log 与首次脚本；修正只涉及探针资源关闭，未改产品。

| tracked 文件 | Direct任务启动/s | 首次快照/s | 相同快照/s | 读取/s | 预览/s | 已确认代码撤销/s |
|---|---:|---:|---:|---:|---:|---:|
| 300 | 0.266 | 7.859 | 5.787 | 1.016 | 3.373 | 22.849 |
| 1500 | 0.240 | 33.003 | 20.976 | 3.473 | 12.163 | 87.423 |

两组 Direct inventory/snapshot调用和快照行均为0。相同快照新增blob均为0，但SQLite产品逻辑字节分别增加61,440和282,624。5项更改各新增5个blob/5,150 bytes；最终CAS分别247/1207个blob、251,872/1,235,312 bytes。以上为单次温缓存样本，未据此声称普遍提速或整体Host启动成本。
