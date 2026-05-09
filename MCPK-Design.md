# MCPK 容器格式设计文档

**MeCapsule Package (MCPK) v1.0**

> 个人知识管理专用的自定义二进制容器格式，用于将文档、图片、音频及其元数据打包为单一文件。

---

## 1. 设计目标

| 目标 | 说明 |
|------|------|
| 单一文件 | 所有资源和元数据合并为一个 `.mcpk` 文件 |
| 流式写入 | 顺序写入 blob，最后写 TOC，适合打包大量文件 |
| 增量读取 | 通过 TOC 索引直接跳转到任意条目，无需顺序扫描 |
| 自描述 | 文件头包含完整布局信息，不依赖外部配置 |
| 可扩展 | 预留 flags 和 metadata JSON，未来可加加密、签名等 |
| 压缩感知 | 每条目独立压缩，已压缩格式（JPEG/MP3）跳过压缩 |

**非目标：** 跨工具互操作（这是自定义格式，需要专用读写工具）。

---

## 2. 文件整体布局

```
┌──────────────────────────────────────────────────┐
│                  FILE HEADER (64 B)               │  偏移 0
├──────────────────────────────────────────────────┤
│                                                  │
│              ENTRY BLOBS (变长)                    │  偏移 64
│                                                  │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│   │ Entry 0  │  │ Entry 1  │  │ Entry 2  │ ...  │
│   │  data    │  │  data    │  │  data    │      │
│   └──────────┘  └──────────┘  └──────────┘      │
│                                                  │
├──────────────────────────────────────────────────┤
│               TOC (目录表, 变长)                   │  偏移 toc_offset
│                                                  │
│   ┌─────────────┐  ┌─────────────┐              │
│   │ TOC Entry 0 │  │ TOC Entry 1 │ ...          │
│   └─────────────┘  └─────────────┘              │
│                                                  │
├──────────────────────────────────────────────────┤
│               FOOTER (16 B)                      │  偏移 footer_offset
└──────────────────────────────────────────────────┘
```

写入顺序：Header → Blob 0 → Blob 1 → ... → Blob N → TOC → Footer。

读取顺序：Footer（定位 TOC）→ TOC（获取所有条目索引）→ 按需跳转到任意 Blob。

---

## 3. 各段详细设计

### 3.1 File Header（64 字节, 固定）

| 偏移 | 大小 | 类型 | 字段 | 说明 |
|------|------|------|------|------|
| 0 | 4 | char[4] | magic | 固定 `MCPK` |
| 4 | 2 | uint16_le | version | 格式版本，当前 `1` |
| 6 | 2 | uint16_le | flags | 全局标志位（见 §3.6） |
| 8 | 8 | uint64_le | created_at | 创建时间戳 (Unix ms) |
| 16 | 8 | uint64_le | toc_offset | TOC 起始偏移 |
| 24 | 8 | uint64_le | toc_size | TOC 字节数 |
| 32 | 4 | uint32_le | entry_count | 条目总数 |
| 36 | 4 | uint32_le | data_section_size | 数据区总字节数 |
| 40 | 24 | bytes | reserved | 保留，全零 |

**设计决策：**
- Header 固定 64 字节，对齐到缓存行，读取只需一次 I/O。
- `toc_offset` 和 `toc_size` 写在 header 中，使读取工具不必先扫描到文件末尾。
- `created_at` 用毫秒级 Unix 时间戳，精度足够且跨平台。

### 3.2 Entry Blob（数据区）

紧接 Header 之后，按 TOC 中的顺序连续存放各条目的实际数据。

每个 Blob 的结构：

```
┌────────────────────────────┐
│ blob_data (stored_size B)  │
└────────────────────────────┘
```

- 如果 `compression != 0x00`，`blob_data` 是压缩后的数据，读取时需解压。
- `original_size` 存在 TOC Entry 中，用于校验解压后的大小。

### 3.3 TOC Entry（目录条目, 每条变长）

```c
struct TocEntry {
    uint8_t   entry_type;        // 0x01=文档, 0x02=图片, 0x03=音频
    uint8_t   compression;       // 0x00=无, 0x01=zlib, 0x02=zstd, 0x03=lz4
    uint8_t   reserved[2];       // 保留
    uint32_t  crc32;             // 原始数据 CRC32
    uint64_t  created_at;        // 创建时间戳 (Unix ms)
    uint64_t  original_size;     // 原始文件大小
    uint64_t  stored_size;       // 存储大小 (压缩后)
    uint64_t  blob_offset;       // blob 在文件中的绝对偏移
    uint16_t  name_len;          // 文件名字节数
    char[]    name;              // UTF-8 文件名
    uint16_t  mime_len;          // MIME 类型字节数
    char[]    mime_type;          // UTF-8 MIME 类型
    uint16_t  meta_len;          // 元数据 JSON 字节数 (0 = 无)
    char[]    metadata;          // UTF-8 JSON 元数据 (可选)
};
```

**字段说明：**

- `entry_type`：用于客户端快速分类，不必解析 MIME。
- `compression`：每条目独立压缩策略。JPEG/PNG/MP3 等已压缩格式设为 `0x00`。
- `crc32`：对**原始数据**（解压前的原始文件内容）计算 CRC32，用于完整性校验。
- `blob_offset`：相对于文件起始的绝对偏移，允许随机访问。
- `metadata`：可选的 JSON 字符串，存放该条目特有的结构化元数据（见 §4）。

### 3.4 TOC 区域

所有 TOC Entry 紧密排列，无额外对齐填充。TOC 区域的起始偏移和总大小记录在 File Header 中。

### 3.5 Footer（16 字节, 固定）

| 偏移 | 大小 | 类型 | 字段 | 说明 |
|------|------|------|------|------|
| 0 | 4 | char[4] | magic | 固定 `MCPK`（用于从尾部验证） |
| 4 | 8 | uint64_le | toc_offset | TOC 起始偏移（冗余，便于从尾部定位） |
| 12 | 4 | uint32_le | footer_crc | footer 自身前 12 字节的 CRC32 |

Footer 的存在允许读取工具从文件末尾反向定位 TOC，无需先读 header。

### 3.6 Flags

| Bit | 名称 | 说明 |
|-----|------|------|
| 0 | ENCRYPTED | 全局加密标志（预留） |
| 1 | SIGNED | 全局签名标志（预留） |
| 2-15 | reserved | 保留 |

---

## 4. 元数据 Schema

### 4.1 文档元数据 (`entry_type = 0x01`)

```json
{
  "title": "季度报告",
  "author": "张三",
  "created": "2026-01-15",
  "modified": "2026-03-20",
  "tags": ["工作", "Q1", "报告"],
  "language": "zh-CN",
  "parent_id": "root",
  "custom": {}
}
```

### 4.2 图片元数据 (`entry_type = 0x02`)

```json
{
  "title": "团队合照",
  "camera": "iPhone 15 Pro",
  "width": 4032,
  "height": 3024,
  "gps": {
    "lat": 39.9042,
    "lng": 116.4074
  },
  "tags": ["团队", "2026"],
  "thumbnail_of": null,
  "parent_id": "root",
  "custom": {}
}
```

### 4.3 音频元数据 (`entry_type = 0x03`)

```json
{
  "title": "会议录音",
  "artist": "",
  "album": "",
  "duration_ms": 3600000,
  "sample_rate": 44100,
  "channels": 2,
  "tags": ["会议", "产品评审"],
  "transcript_available": false,
  "parent_id": "root",
  "custom": {}
}
```

### 4.4 通用字段

所有类型的元数据都支持以下通用字段：

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `title` | string | 是 | 显示标题 |
| `tags` | string[] | 否 | 标签列表 |
| `parent_id` | string | 否 | 父条目 ID（用于构建树形结构） |
| `custom` | object | 否 | 用户自定义扩展 |

---

## 5. 条目关系

条目之间通过 `parent_id` 和 `metadata.relationships` 建立关系：

```json
{
  "relationships": [
    { "type": "thumbnail", "target_id": "img_001" },
    { "type": "attachment", "target_id": "doc_003" },
    { "type": "transcript", "target_id": "txt_002" }
  ]
}
```

关系类型：

| type | 说明 | 典型场景 |
|------|------|----------|
| `thumbnail` | 缩略图 | 文档/音频的封面图 |
| `attachment` | 附件 | 文档关联的图片或音频 |
| `transcript` | 转写文本 | 音频的文字转录 |
| `annotation` | 批注 | 对某条目的标注 |
| `related` | 相关 | 语义关联 |

---

## 6. 压缩策略

| entry_type | 典型格式 | 推荐压缩 | 理由 |
|------------|----------|----------|------|
| 文档 | .md, .txt, .json | zlib (0x01) | 文本压缩比高 (5-10x) |
| 文档 | .pdf | 无 (0x00) | PDF 内部已压缩 |
| 图片 | .jpg, .png, .webp | 无 (0x00) | 已压缩格式 |
| 图片 | .bmp, .tiff | zlib (0x01) | 未压缩格式 |
| 音频 | .mp3, .aac, .ogg | 无 (0x00) | 已压缩格式 |
| 音频 | .wav, .flac | zstd (0x02) | WAV 未压缩，FLAC 可进一步压缩 |

写入工具应自动判断：检测 MIME 类型 → 选择压缩策略 → 写入 TOC 中的 `compression` 字段。

---

## 7. 完整性校验

- 每个条目的 `crc32` 字段存储原始数据的 CRC32 校验值。
- 读取时：读取 blob → 如有压缩则解压 → 计算 CRC32 → 与 TOC 中的值比对。
- Footer 的 `footer_crc` 校验 footer 自身完整性。
- 全局签名（`SIGNED` flag）为预留功能，v1 不实现。

---

## 8. 实现指南

### 8.1 写入流程

```
1. 创建文件，写入 Header（toc_offset=0, toc_size=0, 先占位）
2. 记录当前偏移 cursor = 64
3. 对每个文件：
   a. 读取原始数据
   b. 计算 CRC32
   c. 判断是否压缩 → 压缩（如需要）
   d. 写入 blob，记录 blob_offset = cursor, stored_size
   e. cursor += stored_size
   f. 构建 TocEntry（所有字段）
4. 记录 toc_offset = cursor
5. 按顺序写入所有 TocEntry
6. 记录 toc_size = 写入的总字节数
7. 写入 Footer
8. 回到文件开头，更新 Header 中的 toc_offset, toc_size, entry_count, data_section_size
```

### 8.2 读取流程

```
1. 读取 Footer（文件末尾 16 字节）
2. 验证 Footer magic == "MCPK"
3. 从 Footer 获取 toc_offset
4. 读取 Header（前 64 字节），验证 magic 和 version
5. 跳转到 toc_offset，解析所有 TocEntry
6. 按需操作：
   - 列出所有条目 → 直接从 TOC 获取
   - 提取某条目 → 从 blob_offset 读取 stored_size 字节 → 解压（如需要）→ 校验 CRC32
   - 提取全部 → 按 TOC 顺序依次提取
```

### 8.3 错误处理

| 情况 | 处理 |
|------|------|
| Magic 不匹配 | 报错 "不是有效的 MCPK 文件" |
| Version 不支持 | 报错 "需要更高版本的读取工具" |
| CRC32 不匹配 | 报错 "数据损坏"，跳过该条目 |
| TOC 解析失败 | 尝试从 Header 中的 toc_offset 读取 |

---

## 9. 安全考虑

- **路径穿越：** 读取工具必须校验 `name` 字段不包含 `../` 或绝对路径。
- **大小限制：** 单个条目不超过 4 GB（uint32 存储大小限制，可扩展到 uint64）。
- **文件总数：** entry_count 为 uint32，最多约 42 亿条目。
- **加密（预留）：** v1 不实现，但 flags 中预留了 ENCRYPTED 位。未来可在 blob 级别加密。

---

## 10. 与 ZIP 方案的对比

| 维度 | MCPK | ZIP |
|------|------|-----|
| 工具依赖 | 需要专用工具 | 系统原生支持 |
| 写入模式 | 流式顺序写入 | 需要重写中央目录 |
| 增量追加 | 支持（更新 footer） | 需要重写整个文件 |
| 元数据 | 内建 JSON schema | 依赖外部文件 |
| 压缩 | 每条目独立选择 | 全局策略 |
| 随机访问 | ✅ 通过 TOC 直接跳转 | ✅ 通过中央目录 |
| 分享便利性 | 低（需工具） | 高 |

---

## 11. 文件扩展名和 MIME 类型

- 扩展名：`.mcpk`
- MIME 类型：`application/x-mecapsule-package`

---

## 12. 版本演进

| 版本 | 状态 | 特性 |
|------|------|------|
| v1.0 | 已完成 | 基础格式：Header / TOC / Footer / 文档 / 图片 / 音频 / zlib / CRC32 |
| v2.0 | 已完成 | Magic Index + 分组存储 + Group Index + VIDEO 类型（见 [MCPK-v2-Design.md](MCPK-v2-Design.md)） |
| v2.1 | 已完成 | 时间戳 + XOR 流加密（见 [MCPK-v2.1-Design.md](MCPK-v2.1-Design.md)） |
| v2.2 | 已完成 | AES-256-GCM + 标签 + 组内关系 + 索引打包（见 [MCPK-v2.2-Design.md](MCPK-v2.2-Design.md)） |

未来计划见 [ROADMAP.md](ROADMAP.md)。

版本兼容规则：读取工具必须拒绝 `version > 工具支持版本` 的文件。
