# Attachments
把用户显式提供的图片和文本文件规范化为有界、内容寻址、可完整性验证的本地附件。

## 边界
- 负责：从显式路径、字节或 Windows 剪贴板摄取附件，验证类型、大小、像素、文本编码和数量预算，并生成不可变附件引用；剪贴板既接受单张位图，也接受一次复制的多张图片文件。
- 负责：图片经完整解码后统一重编码为无元数据 PNG；文本只接受有界 UTF-8；拒绝动画、PDF、音视频和任意二进制。
- 负责：把规范化内容原子保存到产品状态目录的 SHA-256 内容寻址仓库，并在每次读取时复核路径、大小和摘要。
- 负责：工作区路径遵守敏感文件、忽略规则和 reparse/symlink 边界；工作区外只接受用户入口明确给出的绝对普通文件，原路径不进入引用、会话或 Provider。
- 不负责：TUI/CLI 渲染、模型能力选择、Provider schema、会话数据库、任务授权或从模型/tool 请求任意路径。

## Units
- `AttachmentStore.put(...)`、`read(ref)`: 原子保存并逐次复核 SHA-256 内容寻址 blob | 本地产品状态目录 I/O | 路径由 digest 唯一推导，不信任 resolver 返回内容
- `AttachmentIngestor.ingest_path(...)`、`ingest_paths(...)`、`ingest_clipboard()`、`ingest_clipboard_items()`: 摄取显式用户附件并规范化为 PNG 或 UTF-8 | 受保护文件读取与 Pillow 解码 | 旧单图入口保持兼容，批量入口接收草稿剩余数量/字节预算并在永久发布前完成校验；多文件必须全是支持的图片并沿用原子 staging，单张位图在 PNG 编码前校验动画、尺寸和像素；外部路径必须绝对且显式
- `AttachmentLimits`: 冻结数量、输入/输出字节与像素预算 | 无副作用 | 不得超过 Core Message 的总容量
