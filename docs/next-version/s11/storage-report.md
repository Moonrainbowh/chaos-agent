# S11 Sessions Memory 存储与检索

源码范围已经停止修改，实际路径与 raw SHA256 见 `storage-files.json`。未改 schema、candidate、Host/UI、原 authentication，也未提交或推送。默认用户数据库未打开；新测试仅显式 TemporaryDirectory 路径，Repo 构造前断言绝对路径归属。

## 行为与 API

- `search_memories/search_memory_diagnostics` 保持既有参数，新增 `context`、`require_applicable=False`、`applicability=None`、`offset=0`、`max_bytes=1MiB`。Host 用 `context={可信当前事实}`、`require_applicable=True`：无条件 active 或声明条件全部吻合才进入词法匹配及 LIMIT；缺事实/冲突在分页前排除。纯旧调用仍兼容不限适用条件。
- 检索 SQL 先约束允许 scope、最新 revision、active 和适用条件；英文 Unicode casefold、连续汉字双字词元，单字可检索。64词元上限；按匹配词元数量、更新时间、ID/revision 稳定排序，offset 仅截取匹配结果。旧相关记录不因最新不相关或冲突记录而漏召回。
- 返回行在正文解码前 SQL 读取 UTF-8 长度并校验总页字节；单条过大检索候选不会进入词法回调。默认1MiB、显式最大16MiB；条件回调前 SQL 128KiB预检。SQL回调逐行匹配，无全 active 正文列表、无向量库。超出总页字节返回明确错误，调用方缩小页或显式扩大额度。
- diagnostics 使用相同排序和条件，返回允许 scope、latest 候选数、match_count、选中 ID@revision 与排除计数，不泄漏其他 scope 正文。`excluded.query` 同时包含单条字节额度排除；不将此计数宣称纯词法失败。
- `get_memory(memory_id, *, scope_type, scope_id, revision=None, max_bytes=1MiB)` 显式 scoped read；允许审阅 inactive/历史。`revise_memory/set_memory_lifecycle/delete_memory` 新增可选 scope 双参数，同事务核对，Host必须传入。`list_memories` 新增 offset/max_bytes，非历史仅最新 active。
- revise 未传条件/来源时保留已有值，显式 `{}` 才清空；适用性部分已知条件吻合但其余缺失为 needs_check，布尔与数值 JSON 条件严格区分，冻结 tuple 与原 JSON list 一致比较。无条件 assess 仍 needs_check，不伪造为任务事实。
- `create_memory(supersedes=...)` 在事务中拒绝跨 scope 替代，失败完全回滚。
- 显式 delete 在既有 forget 表留下各 revision 正文 SHA256 与 `id:` 域分离 ID 哈希；同 scope 原 ID 新正文、同正文新 ID、修订转入被忘记内容及旧候选激活都拒绝。其他同 scope 相同正文副本撤回，防已存在副本继续自动注入。数据库重开仍保留屏障；不删除其他正文或原历史数据库。

## 验证

使用 candidate 锁定 CPython3.13.2 与 main PYTHONPATH。

- `storage-tests-final.log`：8 tests，0 failures/errors，3.236s。包括 120 新无关+30冲突+30缺事实后旧中文召回、跨 project/user 排除、分页相关度、条件严格比较、真实关闭重开 forget、scoped CRUD/CAS、跨 scope 替代回滚、2MiB正文解码前字节拒绝。
- `storage-sessions-tests.log`：Sessions 全套263 tests，2 skip，0 failures/errors，55.502s。末次条件类型比较修复另由上述8测试通过；最终 frozen 全套由 Root 执行。
- 限定路径 `git diff --check` PASS。

这只完成 Sessions 子范围；默认 semantic 注入、可信 project identity、用户命令入口和整阶段独立监督由 Root 集成验证。

## 默认规则链修复

Root 实测 Sessions 完整规则链6248超过6000，已在原 `Sessions/AGENTS.md` 同义压缩重复职责前缀、冗长流程描述与相关不负责条目，强制约束、CAS/身份/forget/预算/recovery边界和全部主要Units保留；没有移到非默认文档。真实 RuleLoader.render 现为5888（减少360），全部六条生产规则链通过，见 `storage-rule-probe.log`/`rule-chains.json`。规则额度6000和总提示20000不变，manifest已更新契约raw hash，源码再次停止写入。
