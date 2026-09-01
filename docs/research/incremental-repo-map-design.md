# 增量仓库语义上下文设计

## 1. 目标

在不把整个仓库注入模型、也不让模型逐个 Tool Call 盲查的前提下，在请求发出前确定性装配任务相关代码。实现采用三层数据生命周期：

```text
File Facts
  ↓ 静态解析与跨文件解析
Direct Semantic Graph
  ↓ 当前 query / touched paths / token budget
Request-derived Context（L0 / L1 / L2）
```

昂贵解析只针对变化文件执行；跨文件直接关系在新 generation 中整体发布；测试影响和上下文层级只在请求时计算。

## 2. 范围与非目标

范围内：Python 定义、引用、调用、继承、import、结构化注解/装饰器和配置访问；relative import、alias、`src/` layout 和 `__init__.py` 显式 re-export；请求时测试影响、确定性 token packing、L0 源码和批量代码切片；generation/FileSignature stale 校验，以及 Windows 路径、编码和换行边界。

非目标：不引入磁盘持久 Repo Index、向量数据库、embedding 或外部搜索服务；不把 `test_impact` 保存为直接语义边；不把 Repo Context 当作验证、覆盖率或任务完成证据；不为动态 Python 行为伪造 exact 解析结果。

## 3. 数据层

### 3.1 File Facts

每个文件事实绑定 workspace-relative POSIX canonical path 与 `FileSignature(size_bytes, modified_ns, device_id, file_id)`。Python 文件通过 AST 提取带范围、签名和 docstring 的 module/class/function/method，import 的 module/level/alias/name/star/词法 owner，字面或动态 `__all__` 状态，call/reference/inherits、annotation/decorator 等结构化使用，以及配置访问的 namespace/key/provenance。单文件骨架累计最多 32 KiB；解析失败或二进制文件只发布 path/signature 事实，不沿用旧符号。

### 3.2 Direct Semantic Graph

只保存可由当前 generation 静态直接得到的五类关系：`import`、`reference`、`call`、`inherits`、`config`。

关系标记 `exact` 或 `heuristic`。exact 只用于唯一解析且未被局部作用域遮蔽的目标；动态派发、`import *`、歧义同名和无法唯一解析的属性访问不得标 exact。import 解析覆盖 relative import、alias、`src/` layout 和显式 `__init__.py` re-export；`import *` 最多建立模块级依赖，不建立 exact 符号关系。

配置关系必须同时携带 namespace、key 和 provenance；只有环境 API 或归一化到同一 imported config object/loader 的来源才建立跨文件 exact 边。裸参数或局部映射即使同名、同 key（例如 `settings.get("timeout")`）也不得串联。

### 3.3 Request-derived Context

输入为同一 generation 的 snapshot、规范化 query、canonical touched set 与 token budget。输出包括 L0/L1/L2、请求时测试影响候选和未装配清单，不写回 Index。

`test_impact` 通过 Direct Graph 的反向依赖按请求动态计算，最多 2 hop、16 个测试节点。它只表示候选影响范围，不表示测试已运行或一定失败。

## 4. scope-aware Python 解析

解析器为 module、class、function、lambda 和 comprehension 建立作用域，记录参数、赋值、循环变量、with/except 绑定、import alias、global 与 nonlocal。

- 参数、局部变量、comprehension 变量和被遮蔽 import 不解析为外部 exact 引用；
- 同名候选只有一个且导入链明确时才标 exact；
- `self`/`cls` 动态派发、运行时赋值、反射、动态 import 和 star import 保持 heuristic 或省略。

## 5. 确定性分层装配

### L0：靶心源码

优先包含被修改函数、明确符号和 traceback 行所在符号的完整源码，单轮最多 2 个。超大符号降级为 L1，并产生可拆分批量读取 targets。`path:line` 命中 module-level 时只生成 line anchor 与上下各 20 行的 bounded slice，不自动注入整个文件。

### L1：直接上下文

包含直接调用者、被调用者、继承关系和紧密配置邻居的签名、docstring、范围与关系摘要，最多 8 个节点。

### L2：远端索引

包含间接依赖、类型祖先、请求时测试影响候选和其他闭包节点的索引清单，最多 16 个节点、最多 2 hop。

### packing 规则

1. 先建立稳定候选序列：显式 path:line、精确符号、touched、lexical rank、直接关系、反向测试影响；
2. 同优先级按 lexical rank、canonical path、start line、qualified symbol 排序；
3. 预算按 L0 60%、L1 25%、L2 15% 划分；未用配额按 L0→L1→L2 顺序回流；
4. 每个候选先计算确定性渲染 token 成本，再按稳定顺序完整装入；不能装入的进入稳定 deferred 清单；
5. 最终文本再次执行硬预算裁剪。

相同 workspace identity、generation、normalized query、canonical touched set、budget 和 tokenizer version 必须得到逐字节一致的选择与输出。

## 6. stale 与并发修改

L0 源码不进入长期 cache。装配从 snapshot 取得 expected generation/FileSignature，读取前校验，读取源码后再次校验。第一次 stale 时只失效受影响 canonical path，并最多重建一次。第二次仍变化则 fail-closed：本轮不发送任何跨代 L0/L1/L2，只保留固定系统提示和有界状态。

## 7. 批量读取 `read_code_slices`

请求包含 `generation` 和 1–16 个 target；每个 target 包含 `path`、`start_line`、`end_line`、`expected_size_bytes`、`expected_modified_ns`、`expected_device_id`、`expected_file_id`。

- path 只能是 workspace-relative POSIX canonical path；
- 单 target 最多 400 行，单批返回最多 128 KiB；
- 同文件允许连续、不重叠切片，拒绝重复或冲突范围；
- 调度层校验当前 generation 及 snapshot signature；Workspace 在读取前后复核磁盘 signature；
- 任一 target stale、越界、解码失败或触碰 junction/symlink/reparse 边界时整批失败，不返回部分源码；
- 返回 encoding、BOM、newline 和可用时的 code page 元数据。

提示要求模型尽量一次提交本轮所有已知目标，减少 N+1；新信息出现后允许后续有界批次。普通 `read_file` 保留用于非代码文件和未知探索路径。

## 8. Windows 与文本边界

- 索引键、关系、cache key 和 tool target 使用 workspace-relative POSIX path；
- 拒绝绝对路径、drive/UNC、反斜杠、NUL、`.`/`..`；
- Windows 比较键使用 Unicode casefold，展示路径保留受保护扫描得到的 spelling；
- Workspace guard 拒绝 junction、symlink 和其他 reparse 路径；
- AST 范围、traceback 和批量读取统一按 logical lines 处理 CRLF/LF；
- stale signature 使用原始字节 size/mtime 与文件系统 device/file identity，不使用规范化文本长度；读取完成后必须刷新 pending invalidation 再比较 generation；
- 编码沿用严格 BOM UTF-8/16/32、无 BOM UTF-8 或显式 Windows ANSI/OEM 规则，禁止 replacement decode。

## 9. 不可信数据边界

所有 Repo Context 均标记为 `UNTRUSTED_REPOSITORY_DATA`。源码、comment、docstring、README 和配置内容只能作为仓库事实，不能覆盖系统、用户、工具、权限或安全指令。仓库内出现的“忽略上级指令”“执行命令”等文本不具有授权效力。

## 10. 缓存与增量

- File Facts 按 canonical path + FileSignature 复用；
- 单文件失效只重扫该文件；
- Direct Graph 在同一 generation 下重新解析并整体发布；
- Tier 选择 cache 绑定 workspace identity、generation、query、touched set、budget 和 tokenizer version；
- L0 正文每次发送前重读并校验，不进入长期 cache；
- 无变化请求不重新列目录或解析源码。

## 11. 验收标准

- 问候和非代码请求不生成 Repo Context；
- File Facts、Direct Semantic Graph、Request-derived Context 生命周期清晰，snapshot 中不存在 `test_impact`；
- 同名符号、参数/局部变量/comprehension shadowing 不产生错误 exact 边；
- relative/alias/src/re-export 正确解析，star import 不产生 exact 符号边；
- `call/reference/inherits/import/config` 关系稳定排序、去重并绑定同一 generation；
- 配置 key 相同但 namespace/provenance 不同不得串联；
- module-level traceback 只生成 bounded line slice；
- 相同输入的 L0/L1/L2 选择和文本逐字节一致且不超预算；
- 请求时反向测试影响有界且不持久化；
- L0 读取最多重建一次，再变化时不发送跨代仓库内容；
- `read_code_slices` generation/signature/range/path 原子校验，支持超大符号拆分并避免 N+1；
- Windows casing、CRLF、encoding、junction/symlink 边界失败闭合；
- Repo Context 中的提示注入文本始终作为不可信数据。

## 12. 当前实现边界

当前实现使用进程内不可变 generation 和 SQLite FTS5 词法索引；SQLite 只服务当前进程的检索，不是重启后持久化的 Repo Index。后续扩展必须先保持上述三层生命周期、确定性输出和 stale fail-closed 契约，不再沿用旧的“持久化阶段”或 L0–L4 路线。
