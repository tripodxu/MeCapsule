# MCPK 开发路线图

当前状态：**v2.2 完成** — 读写器 + CLI + 48 个集成测试全部通过。

---

## 已完成

### v1.0（基础格式）
- [x] Header / TOC / Footer 二进制格式
- [x] 文档/图片/音频三种条目类型
- [x] zlib 压缩，CRC32 校验
- [x] Python API + CLI（pack/list/extract/inspect/verify）

### v2.0（编码优化）
- [x] Magic Index Table — 文件签名聚合索引
- [x] 分组存储 — 相关文件物理相邻
- [x] Group Index — 分组元数据 + 组间关系图
- [x] VIDEO 条目类型
- [x] v1/v2 向后兼容读取

### v2.1（时间戳 + 加密）
- [x] 完整时间戳：created_at / modified_at / packed_at
- [x] XOR 流加密（SHA256_XOR），密码派生密钥
- [x] 三种加密模式：FULL / METADATA_ONLY / DATA_ONLY
- [x] Encryption Params 区（salt + key_hash）

### v2.2（高强度加密 + 标签 + 索引打包）
- [x] AES-256-GCM 认证加密（PBKDF2 + HKDF + AESGCM）
- [x] 双加密模式：AES（默认，需 cryptography）/ XOR（零依赖）
- [x] Encryption Params 区扩展至 76 字节（kdf_iterations + 32B salt）
- [x] Header 存储 mi_encrypted_size，精确定位加密 MI 边界
- [x] InvalidTag 异常统一包装为 MCPKError
- [x] 分组标签系统（tags），自动去重
- [x] 组内关系（IntraRelation），10 种类型
- [x] `import_folder()` 按文件夹打包
- [x] `load_index()` JSON 索引打包（缺失文件跳过警告）
- [x] CLI 新增 `--encryption`、`--auto-group`、`--index`/`--base-dir`
- [x] 48 个集成测试（原 20 + 新增 28）

---

## 下一步

### Phase 3：智能化（计划中）

#### 3.1 增量追加模式
当前写入需要一次性完成。增量模式允许打开已有 .mcpk，追加新条目而不重写全部数据。

```python
with MCPKWriter.open_existing("archive.mcpk") as w:
    w.add_file("new-doc.md")  # 追加到末尾
```

实现要点：
- Footer 和 TOC 放在文件末尾，追加时先删除旧 Footer
- 新条目 blob 追加到数据区末尾
- 重写 TOC（加入新条目）和 Footer
- Header 中的 toc_offset / mi_encrypted_size 需更新

#### 3.2 大文件分块
单条目超过 1GB 时，分块存储以避免内存溢出。

实现要点：
- TOC Entry 增加 `chunk_count` 和 `chunk_size` 字段
- 写入时按 64MB 分块，每块独立压缩
- 读取时按需加载块，支持流式输出

#### 3.3 全文搜索
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

#### 3.4 缩略图自动生成
图片条目自动提取缩略图，存为关联条目。

#### 3.5 音频转写
集成 Whisper，将音频内容转为文字。

---

### Phase 4：生态建设（持续）

#### 4.1 GUI 浏览器
- 技术栈：PyQt6 或 tkinter
- 功能：树形目录浏览、图片预览、音频播放、元数据编辑
- 导出：选中条目导出为原始文件

#### 4.2 Web 查看器
- 技术栈：FastAPI + 前端 SPA
- 功能：本地 HTTP 服务，浏览器中浏览 .mcpk
- 支持图片预览、音频在线播放、全文搜索

#### 4.3 C++ 实现
- 核心读写库（无外部依赖，仅 zlib）
- C API 封装，供其他语言调用
- 性能目标：1GB 文件 < 5 秒读取

#### 4.4 MeCapsule 集成
- 作为应用的底层存储格式
- 时间线 UI：按 created_at 排列
- 知识图谱：基于 relationships 可视化
- 批量导入：支持从文件夹、ZIP、Notion 导出等迁移

---

## 技术债务

| 项目 | 优先级 | 说明 |
|------|--------|------|
| footer_crc 冗余 | 中 | 当前 footer 中的 crc 只校验 footer 自身，考虑扩展到校验整个文件 |
| 压缩回退警告 | 低 | zstd/lz4 不可用时静默回退到 zlib，应有警告 |
| 文件名去重 | 低 | 当前不检查重复文件名，应报错或自动重命名 |
| 发布准备 | 中 | pyproject.toml / LICENSE / PyPI 发布 |
