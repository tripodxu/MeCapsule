# MCPK 开发路线图

当前状态：**v1.0 核心完成** — 读写器 + CLI 可用，待集成测试验证。

---

## Phase 1：稳固基础（1-2 周）

目标：确保 v1.0 可靠可用。

### 1.1 测试和修复
- [ ] 运行 `test_mcpk.py`，修复所有发现的问题
- [ ] 边界测试：空文件、超长文件名、中文路径、特殊字符
- [ ] 大文件测试：100MB+ 单文件、1000+ 条目打包
- [ ] 损坏测试：截断文件、篡改字节，验证错误处理

### 1.2 类型提示和文档
- [ ] 为所有公开 API 补充完整的 type hints
- [ ] 生成 API 文档（sphinx 或 pdoc）
- [ ] 补充 docstring 中的使用示例

### 1.3 发布准备
- [ ] 创建 `pyproject.toml`，配置打包
- [ ] 添加 `LICENSE` 文件
- [ ] 发布到 PyPI：`pip install mcpk`

---

## Phase 2：格式增强（2-4 周）

目标：补齐设计文档中规划的高级特性。

### 2.1 增量追加模式
当前写入需要一次性完成。增量模式允许打开已有 .mcpk，追加新条目而不重写全部数据。

```python
with MCPKWriter.open_existing("archive.mcpk") as w:
    w.add_file("new-doc.md")  # 追加到末尾
```

实现要点：
- Footer 和 TOC 放在文件末尾，追加时先删除旧 Footer
- 新条目 blob 追加到数据区末尾
- 重写 TOC（加入新条目）和 Footer
- Header 中的 toc_offset / toc_size 需更新

### 2.2 大文件分块
单条目超过 1GB 时，分块存储以避免内存溢出。

实现要点：
- TOC Entry 增加 `chunk_count` 和 `chunk_size` 字段
- 写入时按 64MB 分块，每块独立压缩
- 读取时按需加载块，支持流式输出

### 2.3 加密（v1.1）
```python
with MCPKWriter("secret.mcpk", password="...") as w:
    w.add_file("private.md")
```

实现要点：
- PBKDF2 从密码派生 256-bit 密钥
- 每条目 blob 使用 AES-256-GCM 加密
- Header flags 设置 ENCRYPTED 位
- nonce 存储在 TOC Entry 的 reserved 字段中

---

## Phase 3：智能化（4-8 周）

目标：让 MCPK 不只是存档工具，而是知识管理的核心。

### 3.1 全文搜索
为文本类条目构建轻量级倒排索引。

```python
with MCPKReader("archive.mcpk") as r:
    results = r.search("人工智能")
    # -> [("doc_001", "notes.md", score=0.92), ...]
```

实现要点：
- 中文分词：jieba 或结巴分词
- 倒排索引存储为特殊的隐藏条目（entry_type = 0xFE）
- 索引条目包含：词 → [(条目ID, 词频, 位置列表)]

### 3.2 缩略图自动生成
图片条目自动提取缩略图，存为关联条目。

```python
with MCPKWriter("photos.mcpk") as w:
    w.add_file("big-photo.jpg", auto_thumbnail=True)
    # 自动生成 photo_thumb.jpg，建立 relationship
```

实现要点：
- 使用 Pillow 生成 256x256 缩略图
- 缩略图存为独立条目，relationship type = "thumbnail"
- JPEG 格式存储，quality=80

### 3.3 音频转写
集成 Whisper，将音频内容转为文字。

```python
with MCPKWriter("meetings.mcpk") as w:
    w.add_file("meeting.mp3", transcribe=True)
    # 自动生成 transcript，建立 relationship
```

---

## Phase 4：生态建设（持续）

目标：覆盖更多使用场景。

### 4.1 GUI 浏览器
- 技术栈：PyQt6 或 tkinter
- 功能：树形目录浏览、图片预览、音频播放、元数据编辑
- 导出：选中条目导出为原始文件

### 4.2 Web 查看器
- 技术栈：FastAPI + 前端 SPA
- 功能：本地 HTTP 服务，浏览器中浏览 .mcpk
- 支持图片预览、音频在线播放、全文搜索

### 4.3 C++ 实现
- 核心读写库（无外部依赖，仅 zlib）
- C API 封装，供其他语言调用
- 性能目标：1GB 文件 < 5 秒读取

### 4.4 MeCapsule 集成
- 作为应用的底层存储格式
- 时间线 UI：按 created_at 排列
- 知识图谱：基于 relationships 可视化
- 批量导入：支持从文件夹、ZIP、Notion 导出等迁移

---

## 技术债务

| 项目 | 优先级 | 说明 |
|------|--------|------|
| footer_crc 冗余 | 中 | 当前 footer 中的 crc 只校验 footer 自身，考虑扩展到校验整个文件 |
| entry_type 扩展 | 低 | 当前只支持 3 种类型，未来可能需要 VIDEO(0x04), ARCHIVE(0x05) 等 |
| 压缩回退 | 低 | zstd/lz4 不可用时静默回退到 zlib，应有警告 |
| 文件名去重 | 低 | 当前不检查重复文件名，应报错或自动重命名 |
