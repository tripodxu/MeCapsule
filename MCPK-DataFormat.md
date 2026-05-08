# MCPK 数据格式规范

**MeCapsule Package (MCPK) v1.0 — 数据格式参考**

> 本文档是 MCPK 容器格式的精确二进制规范，供实现读写工具时参考。

---

## 1. 字节序

所有多字节整数采用 **小端序 (Little-Endian)** 存储。

---

## 2. File Header — 精确字节布局

总大小：**64 字节**

```
偏移   大小   类型          字段名           值/说明
──────────────────────────────────────────────────────────────
0x00   4B    char[4]       magic            0x4D 0x43 0x50 0x4B ("MCPK")
0x04   2B    uint16_le     version          0x0001 (v1)
0x06   2B    uint16_le     flags            0x0000 (无标志)
0x08   8B    uint64_le     created_at       Unix 毫秒时间戳
0x10   8B    uint64_le     toc_offset       TOC 区域起始偏移 (字节)
0x18   8B    uint64_le     toc_size         TOC 区域总大小 (字节)
0x20   4B    uint32_le     entry_count      条目总数
0x24   4B    uint32_le     data_section_size 数据区总大小 (字节)
0x28   24B   bytes         reserved         全部填 0x00
──────────────────────────────────────────────────────────────
      64B 总计
```

**示例（十六进制）：**

一个包含 2 个条目、创建于 2026-05-08 的文件：

```
4D 43 50 4B  │ magic = "MCPK"
01 00        │ version = 1
00 00        │ flags = 0
C0 A8 3E 19  │ created_at (低字节)
7A 01 00 00  │
00 00 00 00  │ (高字节)
40 00 00 00  │ toc_offset = 64 + 数据区大小
00 00 00 00  │ (高 4 字节)
B8 00 00 00  │ toc_size = 184
00 00 00 00  │ (高 4 字节)
02 00 00 00  │ entry_count = 2
00 04 00 00  │ data_section_size = 1024
00 00 00 00  │ (高 4 字节)
00 00 ...    │ reserved (24 字节全零)
```

---

## 3. TOC Entry — 精确字节布局

每个 TOC Entry 为变长结构。

```
偏移(相对)  大小     类型          字段名          说明
──────────────────────────────────────────────────────────────
+0x00      1B      uint8_t      entry_type      条目类型 (见 §6)
+0x01      1B      uint8_t      compression     压缩算法 (见 §7)
+0x02      2B      bytes        reserved        保留, 填 0x00
+0x04      4B      uint32_le    crc32           原始数据 CRC32
+0x08      8B      uint64_le    created_at      条目创建时间戳 (Unix ms)
+0x10      8B      uint64_le    original_size   原始文件大小 (字节)
+0x18      8B      uint64_le    stored_size     存储大小 (压缩后, 字节)
+0x20      8B      uint64_le    blob_offset     blob 数据的文件绝对偏移
+0x28      2B      uint16_le    name_len        文件名 UTF-8 字节数
+0x2A      变长    char[]       name            UTF-8 文件名 (无 \0 终止)
+0x2A+N    2B      uint16_le    mime_len        MIME 类型 UTF-8 字节数
+...       变长    char[]       mime_type       UTF-8 MIME 类型 (无 \0 终止)
+...       2B      uint16_le    meta_len        元数据 JSON 字节数 (0=无)
+...       变长    char[]       metadata        UTF-8 JSON (可选)
──────────────────────────────────────────────────────────────
固定部分: 40 字节 (0x00 ~ 0x27)
变长部分: name_len + 2 + mime_len + 2 + meta_len
```

**最小 TOC Entry 大小：** 40 + 1(name_len) + 2 + 1(mime_len) + 2 + 0 = **46 字节**

---

## 4. Footer — 精确字节布局

总大小：**16 字节**

```
偏移   大小   类型          字段名          说明
──────────────────────────────────────────────────────────────
0x00   4B    char[4]       magic           0x4D 0x43 0x50 0x4B ("MCPK")
0x04   8B    uint64_le     toc_offset      TOC 起始偏移 (与 Header 冗余)
0x0C   4B    uint32_le     footer_crc      前 12 字节的 CRC32
──────────────────────────────────────────────────────────────
      16B 总计
```

Footer 固定位于文件末尾 16 字节处。

---

## 5. 完整文件示例

以下是一个包含 2 个条目的最小 MCPK 文件：

```
条目 0: notes.md (128 字节, zlib 压缩后 64 字节)
条目 1: photo.jpg (896 字节, 无压缩)
```

### 5.1 二进制结构

```
[File Header: 64 bytes]
  4D 43 50 4B  01 00  00 00
  C0 A8 3E 19  7A 01 00 00   ← created_at
  40 00 00 00  00 00 00 00   ← toc_offset = 64 + 64 + 896 = 1024
  C8 00 00 00  00 00 00 00   ← toc_size = 200
  02 00 00 00                ← entry_count = 2
  A0 03 00 00  00 00 00 00   ← data_section_size = 64 + 896 = 960
  00 00 ... (24 bytes reserved)

[Entry 0 Blob: 64 bytes]     ← 偏移 64
  [zlib 压缩的 notes.md 内容]

[Entry 1 Blob: 896 bytes]    ← 偏移 128
  [photo.jpg 原始内容]

[TOC: ~200 bytes]            ← 偏移 1024

  TOC Entry 0:
    01                        entry_type = 0x01 (文档)
    01                        compression = 0x01 (zlib)
    00 00                     reserved
    [4B CRC32]                crc32
    [8B timestamp]            created_at
    80 00 00 00  00 00 00 00  original_size = 128
    40 00 00 00  00 00 00 00  stored_size = 64
    40 00 00 00  00 00 00 00  blob_offset = 64
    08 00                     name_len = 8
    6E 6F 74 65 73 2E 6D 64   name = "notes.md"
    1A 00                     mime_len = 26
    74 65 78 74 2F 6D 61 72   mime_type = "text/markdown"
    6B 64 6F 77 6E ...
    [2B meta_len]             如有元数据
    [metadata JSON]           如有元数据

  TOC Entry 1:
    02                        entry_type = 0x02 (图片)
    00                        compression = 0x00 (无)
    00                        reserved
    [4B CRC32]
    [8B timestamp]
    80 03 00 00  00 00 00 00  original_size = 896
    80 03 00 00  00 00 00 00  stored_size = 896
    80 00 00 00  00 00 00 00  blob_offset = 128
    09 00                     name_len = 9
    70 68 6F 74 6F 2E 6A 70  name = "photo.jpg"
    67 ...
    0A 00                     mime_len = 10
    69 6D 61 67 65 2F 6A 70  mime_type = "image/jpeg"
    65 67 ...
    [2B meta_len]
    [metadata JSON]

[Footer: 16 bytes]           ← 偏移 1224
  4D 43 50 4B                magic
  00 04 00 00  00 00 00 00   toc_offset = 1024
  [4B footer_crc]
```

---

## 6. 枚举常量

### 6.1 entry_type

| 值 | 名称 | 说明 |
|----|------|------|
| 0x01 | DOCUMENT | 文档 (.md, .txt, .pdf, .docx, .json) |
| 0x02 | IMAGE | 图片 (.jpg, .png, .gif, .webp, .bmp, .svg) |
| 0x03 | AUDIO | 音频 (.mp3, .wav, .ogg, .flac, .aac, .m4a) |
| 0x04-0xFF | reserved | 保留 |

### 6.2 compression

| 值 | 名称 | 库 | 说明 |
|----|------|----|------|
| 0x00 | NONE | - | 不压缩 |
| 0x01 | ZLIB | zlib / zlib-ng | 通用压缩，兼容性最好 |
| 0x02 | ZSTD | zstd / facebook/zstd | 高压缩比 + 快速解压 |
| 0x03 | LZ4 | lz4 | 极速压缩/解压，压缩比低 |
| 0x04-0xFF | reserved | - | 保留 |

### 6.3 flags (全局)

| Bit | 名称 | 说明 |
|-----|------|------|
| 0 | ENCRYPTED | 文件已加密（v1 预留） |
| 1 | SIGNED | 文件已签名（v1 预留） |
| 2-15 | reserved | 保留 |

---

## 7. MIME 类型参考

### 7.1 文档

| MIME 类型 | 扩展名 | 推荐压缩 |
|-----------|--------|----------|
| text/markdown | .md | zlib |
| text/plain | .txt | zlib |
| application/json | .json | zlib |
| application/pdf | .pdf | 无 |
| application/vnd.openxmlformats-officedocument.wordprocessingml.document | .docx | 无 |
| text/html | .html | zlib |
| text/csv | .csv | zlib |

### 7.2 图片

| MIME 类型 | 扩展名 | 推荐压缩 |
|-----------|--------|----------|
| image/jpeg | .jpg, .jpeg | 无 |
| image/png | .png | 无 |
| image/gif | .gif | 无 |
| image/webp | .webp | 无 |
| image/svg+xml | .svg | zlib |
| image/bmp | .bmp | zlib |
| image/tiff | .tiff | zlib |

### 7.3 音频

| MIME 类型 | 扩展名 | 推荐压缩 |
|-----------|--------|----------|
| audio/mpeg | .mp3 | 无 |
| audio/wav | .wav | zstd |
| audio/ogg | .ogg | 无 |
| audio/flac | .flac | 无 |
| audio/aac | .aac | 无 |
| audio/mp4 | .m4a | 无 |
| audio/x-wav | .wav | zstd |

---

## 8. 元数据 JSON Schema

### 8.1 通用 Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "title": {
      "type": "string",
      "description": "条目显示标题"
    },
    "tags": {
      "type": "array",
      "items": { "type": "string" },
      "description": "标签列表"
    },
    "parent_id": {
      "type": "string",
      "description": "父条目 ID",
      "default": "root"
    },
    "relationships": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "type": {
            "type": "string",
            "enum": ["thumbnail", "attachment", "transcript", "annotation", "related"]
          },
          "target_id": { "type": "string" }
        },
        "required": ["type", "target_id"]
      }
    },
    "custom": {
      "type": "object",
      "description": "用户自定义扩展字段"
    }
  },
  "required": ["title"]
}
```

### 8.2 文档扩展 Schema

```json
{
  "type": "object",
  "properties": {
    "title": { "type": "string" },
    "author": { "type": "string" },
    "created": { "type": "string", "format": "date" },
    "modified": { "type": "string", "format": "date" },
    "language": { "type": "string" },
    "word_count": { "type": "integer" },
    "tags": { "type": "array", "items": { "type": "string" } },
    "parent_id": { "type": "string" },
    "custom": { "type": "object" }
  },
  "required": ["title"]
}
```

### 8.3 图片扩展 Schema

```json
{
  "type": "object",
  "properties": {
    "title": { "type": "string" },
    "camera": { "type": "string" },
    "width": { "type": "integer" },
    "height": { "type": "integer" },
    "gps": {
      "type": "object",
      "properties": {
        "lat": { "type": "number" },
        "lng": { "type": "number" }
      }
    },
    "thumbnail_of": { "type": ["string", "null"] },
    "tags": { "type": "array", "items": { "type": "string" } },
    "parent_id": { "type": "string" },
    "custom": { "type": "object" }
  },
  "required": ["title"]
}
```

### 8.4 音频扩展 Schema

```json
{
  "type": "object",
  "properties": {
    "title": { "type": "string" },
    "artist": { "type": "string" },
    "album": { "type": "string" },
    "duration_ms": { "type": "integer" },
    "sample_rate": { "type": "integer" },
    "channels": { "type": "integer", "enum": [1, 2] },
    "transcript_available": { "type": "boolean" },
    "tags": { "type": "array", "items": { "type": "string" } },
    "parent_id": { "type": "string" },
    "custom": { "type": "object" }
  },
  "required": ["title"]
}
```

---

## 9. 验证规则

读取工具必须执行以下验证：

| 检查项 | 规则 | 失败处理 |
|--------|------|----------|
| Header magic | 前 4 字节 == `MCPK` | 拒绝文件 |
| Header version | version <= 工具支持的最大版本 | 拒绝文件 |
| Footer magic | 文件末尾 4 字节 == `MCPK` | 警告，尝试从 header 恢复 |
| Footer CRC | CRC32(footer[0:12]) == footer_crc | 警告 |
| TOC 一致性 | 解析 entry_count 个条目后恰好消耗 toc_size 字节 | 报错 |
| Blob CRC | CRC32(解压后的数据) == toc_entry.crc32 | 报错，标记条目损坏 |
| Blob 大小 | 解压后大小 == toc_entry.original_size | 报错 |
| 偏移范围 | blob_offset + stored_size <= 文件大小 | 报错 |
| 文件名安全 | name 不包含 `../`、`\`、以 `/` 开头 | 拒绝条目 |

---

## 10. 压缩细节

### 10.1 zlib (0x01)

- 默认压缩级别：6（平衡压缩比和速度）
- Python: `zlib.compress(data, level=6)`
- C++: `deflateInit2(&stream, 6, Z_DEFLATED, 15, 8, Z_DEFAULT_STRATEGY)`

### 10.2 zstd (0x02)

- 默认压缩级别：3（快速且高压缩比）
- Python: `zstd.compress(data, level=3)`
- C++: `ZSTD_compress(dst, dstSize, src, srcSize, 3)`

### 10.3 lz4 (0x03)

- 使用 LZ4 frame 格式
- Python: `lz4.frame.compress(data)`
- C++: `LZ4F_compressFrame(dst, dstSize, src, srcSize, NULL)`

---

## 11. CRC32 计算

- 算法：CRC-32/ISO-HDLC（即标准 CRC32，多项式 0x04C11DB7）
- 输入：原始文件数据（未压缩）
- Python: `binascii.crc32(data) & 0xFFFFFFFF`
- C++: 使用 `<zlib.h>` 中的 `crc32()` 函数

---

## 12. 条目 ID 生成

元数据中的 `parent_id` 和 `relationships[].target_id` 需要稳定的条目 ID。推荐方案：

- 使用文件名（不含扩展名）作为默认 ID，如 `notes.md` → `notes`
- 如有冲突，追加数字后缀：`notes_2`, `notes_3`
- 或使用内容的 SHA-256 前 8 字节作为 ID：`a3f2b1c8`

---

## 13. 实现检查清单

写入工具：
- [ ] 正确写入 64 字节 Header，magic = "MCPK"
- [ ] 按小端序写入所有整数
- [ ] 对每个文件判断压缩策略
- [ ] 计算原始数据 CRC32
- [ ] 按顺序写入 blob
- [ ] 构建完整 TOC
- [ ] 写入 Footer 并计算 footer_crc
- [ ] 回写 Header 中的 toc_offset, toc_size, entry_count, data_section_size

读取工具：
- [ ] 验证 Header magic 和 version
- [ ] 从 Footer 或 Header 定位 TOC
- [ ] 解析所有 TOC Entry
- [ ] 按需提取 blob 并解压
- [ ] 校验 CRC32
- [ ] 防范路径穿越攻击
