# Project Launcher
保存用户明确选择的项目目录和最近项目，为手机终端提供可恢复的启动入口。

## 边界
- 负责：有界项目目录列表、路径验证、最近选择和原子 JSON 持久化。
- 负责：只保存用户指定或 Host 提供的目录；不递归扫描磁盘或猜测工程根。
- 不负责：Agent 运行时、模型调用、会话数据、切换进程 cwd 或 SSH 配置。
- 不负责：复用 Projects 的生态/验证配方发现；此功能是用户项目入口目录。

## Units
- `ProjectEntry`、`ProjectStore.entries/add/select/remove/seed/last_root`：保存最多64个规范化绝对项目目录及最近选择，按最近项目优先投影目录可用性；移除只移除入口并记住历史导入排除记录 | 同目录临时文件 fsync 后原子 UTF-8 JSON 替换 | 相对路径、失效目录、坏状态显式报错且不覆盖原状态；Host 在 `asyncio.to_thread` 内调用阻塞 IO。
- `ProjectStore.browse_roots/child_directories`：提供已登记目录、home与存在的本地盘入口和最多1000个直接子目录 | 只读浅层目录枚举 | 不递归扫描；排除版本管理元数据、依赖缓存目录，不跟随子目录链接。
