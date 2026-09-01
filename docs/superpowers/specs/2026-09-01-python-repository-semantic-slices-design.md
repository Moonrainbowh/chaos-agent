# Python 仓库语义理解与分层上下文设计

## 1. 目标

在现有进程内 Repo Index 基础上补齐 Python 仓库语义理解，并在模型请求发出前，把确定性静态分析结果装配成与当前任务相关的 L0/L1/L2 分层上下文：

- definition/reference；
- import、继承和静态调用关系；
- 带 namespace/provenance 的配置声明、加载与消费链；
- 请求时动态计算的测试影响候选；
- 与当前任务相关的核心源码、紧密骨架和远端索引。

核心目标不是在“极端纯索引”和“极端全量注入”之间二选一，而是让系统在首轮请求前完成静态确定性装配：靶心代码直接进入 Prompt，直接邻居提供骨架，远端闭包保留索引。只有静态装配仍不足时，模型才使用批量切片读取，避免一次一个函数的 N+1 串行往返。

本设计覆盖旧研究文档中“阶段 3 = SQLite 持久化”的安排。新的第三步是 Python 语义关系与 Tiered Context；磁盘持久化移入延期候选。

## 2. 范围

### 2.1 本次包含

- Python 定义符号的起止行、签名和有界 Docstring。
- scope-aware 的 import/reference/call/inherits/config 直接关系解析。
- relative import、alias、`src/` layout、显式 `__init__.py` re-export。
- type annotation、decorator、base class 等结构化引用。
- 请求时从直接语义图反向计算测试影响候选。
- 请求前自动选择、预算打包并装配 L0/L1/L2。
- 当首轮上下文仍不足时，提供带 generation/FileSignature 校验的批量代码切片工具。
- 一文件变更仅重扫该文件，但重新发布内部一致的跨文件关系 generation。
- Windows workspace-relative POSIX canonical path、大小写、CRLF、encoding、junction/symlink 边界。
- Repo Context 的不可信数据隔离。
- 旧研究路线和 Context/Windows 集成契约同步更新。
- 非 Python 语言维持现有定义级索引能力。

### 2.2 本次不包含

- SQLite 或其他磁盘持久 Repo Index。
- Tree-sitter、LSP 服务、Embedding、向量库或新增第三方依赖。
- 运行时 tracing、完整类型推断、动态派发证明或覆盖率证明。
- JavaScript/TypeScript、Go、Rust、Java、C# 的同等语义关系解析。
- 无界源码注入、整个文件或整个仓库自动灌入 Prompt。
- L0-L4 交互式展开工具；本次固定为自动 L0/L1/L2。

## 3. 已有基线

当前主链已有：单文件有界扫描、Python 定义/import、不可变 generation、内存 SQLite FTS、RRF 排名、dirty-path 失效和 Turn View LRU。相关 23 项基线测试通过。测试中已有的 SQLite 连接 `ResourceWarning` 与本功能无关，不在本次顺带修复。

## 4. 总体架构与数据分层

主架构保持“单文件扫描 → 不可变快照 → 请求前分层装配”，但数据职责明确拆为三层：

```text
File Facts
  单文件、无跨文件结论、可增量替换
        ↓ snapshot publication
Direct Semantic Graph
  只保存静态直接边、不可变、generation 一致
        ↓ per request
Request-derived Context
  任务焦点、反向测试影响、L0/L1/L2、临时源码
```

### 4.1 File Facts

File Facts 是单文件扫描结果，只陈述从该文件直接提取的事实：

- `FileSignature`、canonical path、language/parse status；
- definitions、signature、Docstring、start/end line；
- raw imports 与 alias；
- scope-aware raw references/calls；
- base classes、decorators、type annotations；
- 带 namespace/provenance 的配置访问。

File Facts 不包含跨文件目标判断、反向依赖、测试影响或请求相关排名。dirty 文件更新时整体替换该文件 facts。

### 4.2 Direct Semantic Graph

Direct Semantic Graph 在发布 snapshot 时，从完整 File Facts 集合解析直接边：

- `import`；
- `reference`；
- `call`；
- `inherits`；
- `config`。

它只保存能够从静态事实直接推导的边，不保存请求派生结果。`test_impact` 不属于持久直接边，不进入 `RepoRelation.kind`；测试影响在每个请求中根据测试文件的直接边、反向 import/reference/call/inherits 关系动态计算。

### 4.3 Request-derived Context

Request-derived Context 是短生命周期结果，输入为同一 generation 的 snapshot、规范化 query、touched paths 和 token budget，输出为：

- task anchors；
- 请求时反向计算的测试影响候选；
- L0/L1/L2 的确定性选择与打包；
- L0 临时读取的源码正文；
- 批量深入读取建议。

它不写回 File Facts 或 Direct Semantic Graph。L0 正文不进入 snapshot、长期 cache、任务事实、日志或度量。

## 5. Python File Facts 与 scope-aware 解析

### 5.1 定义骨架与上限

`Symbol` 增加兼容的可选字段：

- `end_line`；
- `signature`：只保存声明签名，不保存函数体；
- `docstring`：只保存有界说明文本。

每个 Python 文件最多保留：

- 200 个 definitions；
- 1,000 个 references/calls/structured references；
- 100 个 config accesses；
- 2,000 条解析后的直接关系。

单签名最多 512 字符，单 Docstring 最多 1,000 字符，单文件骨架文本合计最多 32 KiB。超过上限时按 canonical path、行号、列号、kind、expression 的稳定顺序裁剪。

### 5.2 词法作用域

AST visitor 建立显式 scope tree，至少区分：

- module；
- class；
- function/async function；
- lambda；
- comprehension。

每个 scope 记录参数、赋值目标、import bindings、函数/类定义、exception target、with target、for target，以及 `global`/`nonlocal` 声明。`Name(Load)` 从当前 scope 向外查找最近绑定：

- 命中参数、局部变量、comprehension/lambda binding 时，不建立跨文件 reference；
- 命中显式 import binding 时，进入 import 解析；
- 命中同模块唯一 definition 时，可建立本地 exact reference；
- 受到 `global`/`nonlocal` 影响时，按声明跳转到对应 scope；
- 同名候选不唯一时，不标 exact。

这套规则用于避免参数、局部变量、shadowing 和同名符号被误连到模块或外部定义。

### 5.3 exact 与 heuristic

`exact` 只用于以下唯一、静态、无歧义关系：

- 最近词法 scope 中唯一的本地 definition；
- 显式 `import module [as alias]` 的模块边；
- 显式 `from module import name [as alias]`，且目标模块中 `name` 唯一可见；
- `module_alias.symbol`，且 alias 和 symbol 都唯一解析；
- 显式 `__init__.py` re-export；
- base class、decorator、type annotation 中满足上述唯一解析规则的符号；
- 相同 namespace + key 且 provenance 兼容的配置直接边。

`heuristic` 只用于有明确静态证据、但无法证明唯一运行时目标的导航关系，例如：

- `self.method()`/`cls.method()` 在当前类层级可定位候选，但可能受 override 或动态替换影响；
- 无法完全确定 packaging root、但仅有一个受边界约束的模块候选；
- 配置对象通过已解析 loader 传递，但消费者类型无法完全确定。

动态 `getattr`、反射、猴子补丁、运行时 import、不唯一 overload/override、`import *` 和无法确定的实例派发不标 exact。缺少足够静态证据时省略关系，而不是强行给 heuristic。

## 6. import 与结构化引用解析

### 6.1 模块索引与 `src/` layout

模块索引只使用受保护工作区中的 Python 文件，并按以下稳定优先级识别 import roots：

1. 明确项目元数据中声明的 package/source root；
2. 工作区直属 `src/`；
3. workspace root。

同一模块在多个 root 中出现且无法由项目元数据消歧时，不标 exact。内部键始终使用 workspace-relative POSIX canonical path。

### 6.2 relative import 与 alias

- relative import 基于 importer 所在 package 和 `level` 逐级解析；越过 package root 时失败闭合。
- `import a.b as c` 记录 `c → a.b`。
- `from a import b as c` 记录 `c → a.b` 或模块 `a` 中导出的 `b`。
- alias shadowed 后，后续引用遵循 scope binding，不继续指向原 import。

### 6.3 `__init__.py` re-export

只把以下情况视为 exact re-export：

- `__init__.py` 中显式 `from .module import Name [as Alias]`；
- 若存在字面量 `__all__`，导出名必须包含在其中；
- re-export 链每次解析有固定深度和 visited set，循环时停止。

`from module import *` 只产生到 module 的 `import` 导航边，不产生符号级 exact reference/call。动态 `__all__` 不用于 exact 判定。

### 6.4 结构化引用

以下 AST 位置按 scope/import 规则产生结构化 reference：

- base classes → `inherits`；
- function/class decorators → `reference`；
- 参数、返回值和变量 type annotations → `reference`；
- type alias、generic 参数和 `typing` 组合中的可解析名称 → `reference`。

字符串 forward reference 只解析语法简单、唯一的限定名称；任意表达式字符串不执行、不求值。

## 7. Direct Semantic Graph 数据模型

`RepoEntry` 携带对应文件的 `FileSignature`，以及排序、去重后的 `RepoRelation`：

```text
RepoRelation(
  kind: import | reference | call | inherits | config,
  source_symbol/source_line,
  target_path/target_symbol/target_line/target_end_line,
  resolution: exact | heuristic,
  reason,
  config_namespace/config_key/config_provenance
)
```

直接图只保存正向边；反向索引可在 snapshot 发布时由这些边确定性构建，但不引入新的语义 kind。`test_impact` 始终属于 Request-derived Context。

## 8. 配置 namespace 与 provenance

配置链不能只因 key 文本相同而串联。每个 `ConfigAccess` 至少包含：

- `namespace`；
- `key`；
- `provenance`；
- `operation`；
- owner/path/line。

namespace 示例：

- `env`：`os.getenv`、`os.environ`；
- `mapping:<qualified-owner>`：特定 config/settings/options/profile 对象；
- `module:<module>`：配置模块公开定义；
- `cli:<parser-owner>`：命令行参数目标。

provenance 描述静态来源，如 env API、具体 loader 符号、imported config object 或 parser definition。建立 exact config 边必须同时满足：

1. namespace 相同；
2. 规范化 key 相同；
3. provenance 相同或存在已解析的直接 loader/reference/call 传递链。

仅 key 相同（例如两个无关模块都使用 `timeout`）不得连边。namespace 可确定但 provenance 不完整时，只在存在唯一静态候选的情况下标 heuristic；否则省略。

配置链只保存 key 和 provenance 元数据，不读取、求值或输出配置值、密钥和环境变量值。

## 9. 请求时测试影响计算

测试文件使用稳定的 canonical test-path 判定。请求时，以任务 anchors 和 L0/L1 生产符号为种子，在 Direct Semantic Graph 的反向索引中展开：

1. 直接 import/reference/call/inherits 到种子的测试符号；
2. 生产代码中的反向直接依赖；
3. 再从这些生产依赖寻找直接测试消费者。

闭包最多 2 hop，最多 16 个测试节点，并受 L1/L2 总节点上限约束。结果只存在于本轮 Request-derived Context，显示为“测试影响候选”，不能宣称测试已执行、代码已覆盖或修改必然导致失败。

## 10. Tiered Context 靶心与层级

### 10.1 task anchor 优先级

装配器从同一 snapshot 和请求输入一次计算 anchors，稳定优先级为：

1. 用户输入或 traceback 中明确的 canonical `path:line`；
2. touched/changed 文件中的精确符号命中；
3. query 中的精确 path/symbol；
4. 词法与结构化融合排名最高的生产符号。

相同优先级按 lexical rank、canonical path、start line、qualified symbol 排序。选择过程不调用模型、不逐文件试读、不跨 generation 混合事实。

### 10.2 L0：核心切片

- 最多 2 个节点。
- 明确落在函数/类范围内的 `path:line`，或精确符号 anchor：注入整个符号源码。
- module-level `path:line`：不得把整个文件作为 L0；改为 line anchor，以目标行为中心读取最多 80 行的 bounded slice，默认前后各 20 行，并受相邻定义边界裁剪。
- L0 必须是完整符号或明确标记的 bounded line slice，绝不把静默截断的函数伪装成完整源码。
- 超大符号无法整体进入本轮预算时降为 L1，同时生成可拆分的批量读取 targets。

### 10.3 L1：紧密上下文

- 最多 8 个节点。
- 包含直接 exact import/reference/call/inherits/config 邻居、最相关 heuristic 邻居和直接测试影响候选。
- 只渲染签名、有界 Docstring、符号范围、关系方向、reason 和 resolution，不包含函数体。

### 10.4 L2：远端闭包

- 最多 16 个节点。
- 包含第二跳依赖、类型/祖先、间接配置节点和其余测试影响候选。
- 只渲染 canonical path、qualified symbol、范围和短关系标签。

## 11. 确定性 token packing

相同 `(workspace identity, generation, normalized query, canonical touched set, token budget, tokenizer version)` 必须产生相同 Tier 选择和文本输出。

### 11.1 候选排序键

所有候选先构造稳定排序键：

```text
(
  tier,
  anchor_priority,
  resolution_priority,   # exact before heuristic
  graph_distance,
  lexical_rank,
  canonical_path,
  start_line,
  qualified_symbol,
  relation_kind
)
```

不得使用文件遍历顺序、set/dict 偶然顺序、壁钟时间或未固定随机数。

### 11.2 原子块与成本

- L0 的完整符号或 bounded line slice 是不可拆原子块。
- L1 的单符号骨架是原子块；Docstring 可按固定字符上限预裁剪，但 packing 后不得二次随机截断。
- L2 的单索引行是原子块。
- 每个原子块先用当前确定性 `estimate_tokens` 计算成本，包含层级 header、路径和边界标记的固定开销。

### 11.3 打包步骤

设扣除固定 header 后的可用预算为 `B`：

1. 初始 quota：`q0=floor(0.60B)`、`q1=floor(0.25B)`、`q2=B-q0-q1`。
2. 各层按稳定排序键在自身 quota 内 first-fit packing；原子块放不下则跳过，不局部截断。
3. 未使用 quota 汇入统一 remainder pool。
4. 所有未放入候选按 `L0 → exact L1 → heuristic L1 → L2` 和各自稳定排序键进行第二次 first-fit packing。
5. 仍放不下的 L0 完整符号降为 L1 骨架，并生成批量读取 targets；module-level bounded slice 不扩成整文件。
6. 最后从已选原子块重新渲染并复核总 token；若估算因固定 header 变化超限，按 `L2 → heuristic L1 → exact L1 → 最低优先 L0` 稳定移除整个原子块，直到满足预算。

packing 不挤占消息最低预算和安全余量。Tier 结构可进入 generation-aware cache；L0 正文不进入长期 cache。

## 12. L0 读取、并发修改与 fail-closed

L0 正文在请求发出前按已选 ranges 对 canonical paths 去重后批量读取：

1. snapshot 提供 expected generation 和每个文件的 expected `FileSignature`；
2. 读取前校验当前 generation 和签名；
3. 读取所有 ranges；
4. 读取后再次校验签名；
5. 全部一致才渲染 L0。

任一文件 stale 时，对受影响 canonical path 进行精确 invalidation 并最多重建一次 Tier Context。第二次读取期间再次变化则 fail-closed：本轮不发送任何 L0 源码，也不发送来自不一致 generation 的 L1/L2；只保留固定系统提示和“仓库上下文因并发变化未装配”的有界状态。不得无限重试或把跨代源码发给模型。

## 13. 批量预测性读取 `read_code_slices`

工具接收：

```text
generation: int
targets: 1..16 个
每个 target:
  path
  start_line
  end_line
  expected_size_bytes
  expected_modified_ns
  expected_device_id
  expected_file_id
```

边界：

- 单 target 最多 400 行；
- 单次合计最多 128 KiB；
- path 必须是 workspace-relative POSIX canonical path；
- 所有 targets 在读取前完整校验，任一 generation/signature/path/range 不匹配则整个调用 stale-fail，不返回部分源码；
- 读取后再次校验全部签名，防止读取过程中的并发修改。

超大符号允许由系统按连续、不重叠、带同一 signature 的 ranges 拆成多个 targets，并在一次或少量有界批次中读取。Prompt 要求模型把本轮已知目标尽量合并，避免 N+1 串行读取；但不规定整个任务生命周期只能调用一次。新发现的目标可以进入后续批量调用。

普通 `read_file` 保留用于非代码文件或事先未知的探索路径。成功批量结果的所有 canonical paths 进入 `TaskState.files_read`。

## 14. Windows canonical path 与文本边界

### 14.1 canonical path

- 内部索引键、relation、cache key、tool target 全部使用 workspace-relative POSIX path：`src/pkg/file.py`。
- 不接受绝对路径、drive/UNC 前缀、反斜杠内部键、NUL、`.`/`..` 逃逸。
- Windows 查找键使用 Unicode casefold 后比较，但保留首次受保护扫描得到的规范 display spelling；仅大小写不同的重复项不得形成两个节点。
- 所有外部输入先经过 Workspace guard 解析，再转 canonical path；不得仅靠字符串前缀判断 containment。

### 14.2 junction/symlink

junction、symlink、reparse point 必须沿用 Workspace guard 和句柄身份校验。解析后的真实目标不在授权 workspace，或路径在扫描与读取间发生身份变化时，失败闭合。canonical path 不能绕过底层身份检查。

### 14.3 CRLF、行号与 encoding

- 行号基于成功解码后的逻辑行，CRLF/LF 都只计一行。
- scanner、`path:line`、Symbol range 和 `read_code_slices` 必须使用同一行拆分函数。
- 返回源码用稳定 `\n` 分隔并携带原 encoding/newline 元数据；不得因 CRLF 产生 off-by-one。
- 只使用 Workspace 现有明确支持的 encoding/BOM 规则；未知或无法无损解码时失败，不猜 legacy code page。
- stale 校验使用原始文件 size/mtime 与 device/file identity，不使用换行规范化后的文本长度；读取后先刷新 pending invalidation，再比较 generation。

## 15. 不可信仓库数据隔离

Repo Context 必须显式标记为 `UNTRUSTED_REPOSITORY_DATA`。源码、comment、Docstring、字符串、文件名和配置 key 都是数据，不是系统或用户指令，不能扩大权限、修改任务、覆盖审批边界或改变工具策略。

渲染要求：

- 固定可信系统说明与不可信数据分开；
- 每个 L0/L1/L2 项使用结构化、长度受限字段和 JSON-compatible escaping；
- 仓库内容中的伪 delimiter、Markdown、XML、prompt 指令或 tool-call 文本只作为转义数据呈现；
- 不把 comment/Docstring 拼进可信指令句；
- Provider Prompt 明确要求忽略仓库数据中的指令性内容，遵循系统与用户指令优先级。

Repo Context 仍只是导航和决策输入，不是完成、授权或验证证据。

## 16. 缓存、增量与一致性

- dirty Python 文件只扫描一次并整体替换该文件 File Facts。
- Direct Semantic Graph 从当前完整 facts 重新解析跨文件直接边，不重读未变化源码。
- snapshot generation 原子发布，前台只读取完整 generation。
- Tier 选择 cache 绑定 workspace identity、generation、normalized query、canonical touched set、budget 和 tokenizer version。
- L0 正文不缓存；每次发送前按 generation/signature 双重校验。
- greeting、非项目目录和关闭 Repo Map 的路径保持零索引更新、零 Tier 装配、零源码注入。

## 17. 错误与真实性边界

- AST/解码失败退化为 path-only 或 L2 path-only。
- 动态或不唯一引用省略或明确标记 heuristic。
- `import *` 不产生符号级 exact。
- `self`/`cls` 动态派发不冒充 exact。
- 配置关系必须有 namespace/provenance，不按裸 key 串联。
- module-level traceback 不扩成整文件。
- L0 只发送完整符号或明确 bounded line slice；预算不足不静默截断。
- stale 或并发变化失败闭合，不发送跨代源码。
- 测试影响是请求派生候选，不是直接边或覆盖率。
- Repo Context 是不可信数据，不能覆盖系统/用户指令。

## 18. 验收标准

### 18.1 数据层与增量

- File Facts、Direct Semantic Graph、Request-derived Context 三层类型和生命周期分离。
- Direct Graph 只含 `import/reference/call/inherits/config`；不存在持久 `test_impact` kind。
- 单文件失效只重扫该文件，跨文件直接关系在新 generation 下同步更新。
- 请求派生测试影响不写回 snapshot，重复请求可由相同直接图确定性重算。

### 18.2 scope 与 import

- 参数、局部变量、comprehension binding、shadowed import 和同名符号不会误连外部 definition。
- `global`/`nonlocal`、relative import、alias、`src/` layout 和显式 `__init__.py` re-export fixture 正确解析。
- `import *`、动态 import、不唯一 source root 和动态 `self` 派发不标 exact。
- base class、decorator、type annotation 能生成符合 exact/heuristic 规则的结构化关系。

### 18.3 配置与测试影响

- config 关系包含 namespace/provenance，两个无关 `timeout` key 不会串联。
- env/config/CLI namespace 不因 key 相同互连。
- 生产 anchor 能在请求时得到直接测试与有界反向依赖候选，但 snapshot 不保存 `test_impact`。

### 18.4 Tier 与 token

- 相同 snapshot/query/touched/budget/tokenizer version 的 Tier 选择和文本逐字节一致。
- 明确函数内 `path:line` 或精确符号任务在首轮获得预算内完整 L0。
- module-level `path:line` 只生成最多 80 行 bounded slice，不注入整个文件。
- L1 只有骨架/Docstring/范围，L2 只有索引。
- 单轮最多 2 个 L0、8 个 L1、16 个 L2、2 hop，三层合计不超预算。
- 超大符号不静默截断，降为 L1 并产生可拆分的批量 targets。

### 18.5 stale、并发与批量工具

- `read_code_slices` 校验 generation、每目标 FileSignature、canonical path 和范围；stale 时原子失败且不返回部分源码。
- 超大符号可拆成多个连续 targets 批量读取；工具鼓励合并已知目标，但允许新信息出现后的后续批次。
- L0 并发修改最多重建一次；再次变化 fail-closed，不发送跨代 L0/L1/L2。
- 成功批量读取的 canonical paths 稳定进入 `TaskState.files_read`。

### 18.6 Windows 与不可信数据

- Windows path casing 只产生一个 canonical 节点；内部路径统一为 workspace-relative POSIX。
- CRLF/LF 的相同行号 fixture 一致，受支持 encoding/BOM 正确，未知编码失败闭合。
- junction/symlink 指向工作区外或读取中身份变化时拒绝。
- 源码/comment/Docstring 中的 prompt injection、伪 delimiter 和 tool 文本被当作转义后的 `UNTRUSTED_REPOSITORY_DATA`，不能改变系统/用户指令或权限。

### 18.7 关键负例测试

至少覆盖：

- 跨模块同名符号；
- 参数/局部变量/import shadowing；
- `self.method()` override/动态派发；
- module-level traceback；
- `import *` 与动态 `__all__`；
- 两个 namespace/provenance 不同但 key 同为 `timeout` 的配置；
- stale range、读取中二次修改和第二次重建仍变化；
- Windows path casing、CRLF、受支持/未知 encoding；
- junction/symlink 越界；
- comment/Docstring 中的仓库 prompt injection。

### 18.8 回归

- unchanged Turn View 不重新列目录或重建索引；L0 临时读取有 generation/signature 保护。
- greeting/no-project、缓存、预算和非 Python 现有行为不回归。
- Context、Core、Workspace、Policy、Windows 集成相关测试和完整仓库测试通过；仅允许记录经证明的无关既有失败。

## 19. 执行与交付契约

- 执行 ID：`628b8b1e-b1ec-43fc-847c-3c98f56e3a84`。
- 路线：原地、inline、TDD；先完成 Feature 实现，再完成 Windows 集成，最后统一审查与验证。
- 工作区：`F:\code-ai-chaos\chaos-16-agent\chaos-agent` 当前 `main`，起点 `bd2ef0c`。
- 保留已有两个本地提交和未跟踪 `uv.lock`；不得修改、暂存或提交 `uv.lock`。
- 规划文档保留在 `docs/superpowers` 并包含在最终提交中。
- 完整验证和审查后只创建一个本地提交；不 push、不建 PR、不部署、不安装依赖、不清理。
- 无关既有失败只隔离和报告，不擅自扩大修复范围。
