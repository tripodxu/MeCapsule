# MCPK 数据格式规范

**MeCapsule Package (MCPK) v2.2 — 数据格式参考**

> 本文档是 MCPK 容器格式的精确二进制规范，供实现读写工具时参考。
>
> v2 在 v1 基础上新增 Magic Index、分组存储、Group Index、VIDEO 类型、时间戳、加密。
> v2.2 新增 AES-256-GCM 加密、分组标签、组内关系。
> 读取工具通过 `version` 字段区分 v1/v2，向后兼容。

---

## 1. 字节序

所有多字节整数采用 **小端序 (Little-Endian)** 存储。

---

## 2. File Header — 精确字节布局

总大小：**64 字节**

### 2.1 v2 Header（当前版本）

```
偏移   大小   类型          字段名              值/说明
──────────────────────────────────────────────────────────────
0x00   4B    char[4]       magic               0x4D 0x43 0x50 0x4B ("MCPK")
0x04   2B    uint16_le     version             0x0002 (v2)
0x06   2B    uint16_le     flags               bit0=ENCRYPTED, bit1=SIGNED
0x08   8B    uint64_le     packed_at           容器打包时间 (Unix ms)
0x10   8B    uint64_le     ep_offset           Encryption Params 起始偏移 (0=无加密)
0x18   8B    uint64_le     ep_size             Encryption Params 字节数 (0=无加密)
0x20   4B    uint32_le     entry_count         条目总数
0x24   4B    uint32_le     group_count         分组总数
0x28   8B    uint64_le     group_index_offset  Group Index 起始偏移
0x30   8B    uint64_le     group_index_size    Group Index 字节数
0x38   8B    uint64_le     toc_offset          TOC 起始偏移
──────────────────────────────────────────────────────────────
      64B 总计
```

**struct 格式：** `<4sHH Q Q Q I I Q Q Q`

**Magic Index 位置：** 紧接 Header（或 Encryption Params）之后，偏移 = `HEADER_SIZE(64) + ep_size`。

**v1 → v2 变化：**

| 偏移 | v1 字段 | v2 字段 | 说明 |
|------|---------|---------|------|
| 0x04 | version=1 | version=2 | 版本号升级 |
| 0x08 | created_at | packed_at | 改为容器级打包时间 |
| 0x10 | toc_offset | ep_offset | 语义变更 |
| 0x18 | toc_size | ep_size | 语义变更 |
| 0x24 | data_section_size 低 4B | group_count | 语义变更 |
| 0x28-0x2F | reserved | group_index_offset + size | 新增 |
| 0x38-0x3F | reserved | toc_offset | 新增 |

### 2.2 v1 Header（遗留格式）

```
偏移   大小   类型          字段名           值/说明
──────────────────────────────────────────────────────────────
0x00   4B    char[4]       magic            "MCPK"
0x04   2B    uint16_le     version          0x0001
0x06   2B    uint16_le     flags            0x0000
0x08   8B    uint64_le     created_at       Unix 毫秒时间戳
0x10   8B    uint64_le     toc_offset       TOC 起始偏移
0x18   8B    uint64_le     toc_size         TOC 字节数
0x20   4B    uint32_le     entry_count      条目总数
0x24   4B    uint32_le     data_section_size 数据区总字节数
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

每个 TOC Entry 为变长结构。v2 在 v1 基础上新增 `modified_at` 时间戳和 `group_id`。

```
偏移(相对)  大小     类型          字段名          说明
──────────────────────────────────────────────────────────────
+0x00      1B      uint8_t      entry_type      条目类型 (见 §6)
+0x01      1B      uint8_t      compression     压缩算法 (见 §7)
+0x02      2B      bytes        reserved        reserved[0]=group_id (v2), 0xFF=无分组
+0x04      4B      uint32_le    crc32           原始数据 CRC32
+0x08      8B      uint64_le    created_at      源文件创建时间 (Unix ms)
+0x10      8B      uint64_le    modified_at     源文件修改时间 (Unix ms, v2 新增)
+0x18      8B      uint64_le    original_size   原始文件大小 (字节)
+0x20      8B      uint64_le    stored_size     存储大小 (压缩后, 字节)
+0x28      8B      uint64_le    blob_offset     blob 数据的文件绝对偏移
+0x30      2B      uint16_le    name_len        文件名 UTF-8 字节数
+0x32      变长    char[]       name            UTF-8 文件名 (无 \0 终止)
+...       2B      uint16_le    mime_len        MIME 类型 UTF-8 字节数
+...       变长    char[]       mime_type       UTF-8 MIME 类型 (无 \0 终止)
+...       2B      uint16_le    meta_len        元数据 JSON 字节数 (0=无)
+...       变长    char[]       metadata        UTF-8 JSON (可选)
──────────────────────────────────────────────────────────────
固定部分: 50 字节 (0x00 ~ 0x31)
变长部分: name_len + 2 + mime_len + 2 + meta_len
```

**struct 格式（固定部分）：** `<BB2sI Q Q Q Q Q H` = 50 字节

**最小 TOC Entry 大小：** 50 + 1(name_len) + 2 + 1(mime_len) + 2 + 0 = **56 字节**

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

## 5. Encryption Params（v2.1 新增，仅加密文件）

位于 Header 之后、Magic Index 之前。大小取决于 `kdf_type`。

### 5.1 SHA256_XOR 格式（kdf_type=0x01，56 字节）

```
偏移   大小    类型          字段              说明
──────────────────────────────────────────────────────────────
0x00   4B     char[4]       params_magic      "ENC0"
0x04   1B     uint8_t       kdf_type          0x01=SHA256_XOR
0x05   1B     uint8_t       encrypt_mode      0x01~0x03
0x06   2B     bytes         reserved
0x08   16B    bytes         salt              128-bit 随机盐
0x18   32B    bytes         control_key_hash  SHA-256(control_key)
──────────────────────────────────────────────────────────────
        56B 总计
```

### 5.2 PBKDF2_AES 格式（kdf_type=0x02，76 字节）

```
偏移   大小    类型          字段              说明
──────────────────────────────────────────────────────────────
0x00   4B     char[4]       params_magic      "ENC0"
0x04   1B     uint8_t       kdf_type          0x02=PBKDF2_AES
0x05   1B     uint8_t       encrypt_mode      0x01~0x03
0x06   2B     bytes         reserved
0x08   4B     uint32_le     kdf_iterations    PBKDF2 迭代次数 (600000)
0x0C   32B    bytes         salt              256-bit 随机盐
0x2C   32B    bytes         key_verify        SHA-256(master_key + "verify")
──────────────────────────────────────────────────────────────
        76B 总计
```

**密码验证流程：** 读取 salt + key_verify → KDF(password, salt) → master_key → SHA-256(master_key + "verify") == key_verify ?

---

## 6. Magic Index（v2 新增）

紧接 Header（或 Encryption Params）之后，聚合所有条目的文件签名。

### 6.1 头部（12 字节）

```
偏移(相对)  大小   类型          字段名          说明
────────────────────────────────────────────────────────────────
+0x00      4B    char[4]       index_magic     "MGIX"
+0x04      4B    uint32_le     entry_count     条目总数
+0x08      4B    uint32_le     index_size      本段总字节数
────────────────────────────────────────────────────────────────
           12B 固定头部
```

### 6.2 Magic Entry（每条目固定 48 字节）

```
偏移(相对)  大小   类型          字段名          说明
────────────────────────────────────────────────────────────────
+0x00      4B    uint32_le     entry_id        条目序号 (0-based)
+0x04      1B    uint8_t       entry_type      条目类型 (0x01~0x04)
+0x05      1B    uint8_t       group_id        所属分组 ID (0xFF=无分组)
+0x06      2B    uint16_le     magic_len       magic 码实际字节数
+0x08      32B   bytes         magic_bytes     文件签名（最多 32 字节，不足补 0）
+0x28      2B    uint16_le     name_len        文件名字节数
+0x2A      6B    bytes         reserved        保留
────────────────────────────────────────────────────────────────
           48B 固定
```

**加密时：** Magic Index 整体用 control_key XOR 或 AES-GCM 加密。

---

## 7. Group Index（v2 新增）

位于数据区之后、TOC 之前，记录分组元数据和关系。

### 7.1 头部（16 字节）

```
偏移(相对)  大小   类型          字段名          说明
────────────────────────────────────────────────────────────────
+0x00      4B    char[4]       index_magic     "GRPX"
+0x04      4B    uint32_le     group_count     分组总数
+0x08      4B    uint32_le     relation_count  组间关系总数
+0x0C      4B    uint32_le     index_size      本段总字节数
────────────────────────────────────────────────────────────────
           16B 固定头部
```

### 7.2 Group Entry（每分组变长）

```
偏移(相对)  大小     类型          字段名          说明
────────────────────────────────────────────────────────────────
+0x00      1B      uint8_t       group_id        分组 ID (0-based)
+0x01      1B      uint8_t       entry_count     组内条目数
+0x02      2B      uint16_le     group_type      分组类型 (见 §6.4)
+0x04      2B      uint16_le     name_len        分组名称字节数
+0x06      变长    char[]        group_name      UTF-8 分组名称
+...       2B      uint16_le     meta_len        元数据 JSON 字节数 (0=无)
+...       变长    char[]        group_metadata  JSON (可选)
+...       2B      uint16_le     tag_count       标签数量 (v2.2 新增)
+...       变长    tags[]        每个 tag: 2B len + UTF-8
+...       2B      uint16_le     entry_id_count  条目 ID 数量
+...       变长    entry_ids[]   每个 4B (uint32_le)
+...       2B      uint16_le     intra_rel_count 组内关系数量 (v2.2 新增)
+...       变长    intra_rels[]  每条: 4B src + 4B tgt + 2B type + 2B desc_len + desc
────────────────────────────────────────────────────────────────
```

### 7.3 Group Relation（组间关系，变长）

```
偏移(相对)  大小     类型          字段名          说明
────────────────────────────────────────────────────────────────
+0x00      1B      uint8_t       source_group    源分组 ID
+0x01      1B      uint8_t       target_group    目标分组 ID
+0x02      2B      uint16_le     relation_type   关系类型 (见 §6.5)
+0x04      2B      uint16_le     desc_len        描述字节数
+0x06      变长    char[]        description     UTF-8 描述 (可选)
────────────────────────────────────────────────────────────────
```

**加密时：** Group Index 整体用 control_key XOR 或 AES-GCM 加密。

---

## 8. 加密数据区布局（v2.1 新增）

每个 blob 在磁盘上的存储格式取决于加密模式：

### 8.1 不加密

```
[blob_data: stored_size 字节]
```

### 8.2 SHA256_XOR 加密

```
[entry_salt: 16B] [XOR 加密的 blob_data]
stored_size = 16 + len(blob_data)
```

### 8.3 PBKDF2_AES 加密

```
[entry_salt: 16B] [AES-GCM: nonce(12B) + ciphertext + tag(16B)]
stored_size = 16 + 12 + len(ciphertext) + 16
```

每条目使用独立密钥：`blob_key_i = HKDF(salt=entry_salt, info="mcpk-blob"+entry_id).derive(data_key_base)`

---

## 9. 完整文件布局（v2）

```
写入顺序: Header → [Encryption Params] → Magic Index → Data Section → Group Index → TOC → Footer
读取顺序: Footer → Header → [Encryption Params] → Magic Index（快速概览）→ TOC（完整索引）→ 按需读取 Blob

┌────────────────────────────────┐
│  File Header (64B)             │  明文，version=2
├────────────────────────────────┤
│  Encryption Params (56/76B)    │  [加密] 明文，含 salt + key_verify
├────────────────────────────────┤
│  Magic Index (12 + 48×N B)    │  文件签名 + 类型 + 分组 [可加密]
├────────────────────────────────┤
│  Data Section (变长)           │  按分组排列的 blobs [可加密]
│    [Group 0 Blobs]             │
│    [Group 1 Blobs]             │
│    [Ungrouped Blobs]           │
├────────────────────────────────┤
│  Group Index (变长)            │  分组 + 标签 + 组内关系 + 组间关系 [可加密]
├────────────────────────────────┤
│  TOC (变长)                    │  条目索引 + 元数据 [可加密]
├────────────────────────────────┤
│  Footer (16B)                  │  明文，magic + toc_offset + crc32
└────────────────────────────────┘
```

---

## 10. 完整文件示例（v1 遗留）

以下是一个包含 2 个条目的最小 MCPK 文件：

```
条目 0: notes.md (128 字节, zlib 压缩后 64 字节)
条目 1: photo.jpg (896 字节, 无压缩)
```

### 10.1 二进制结构

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

## 11. 枚举常量

### 11.1 entry_type

| 值 | 名称 | 说明 |
|----|------|------|
| 0x01 | DOCUMENT | 文档 |
| 0x02 | IMAGE | 图片 |
| 0x03 | AUDIO | 音频 |
| 0x04 | VIDEO | 视频（v2 新增） |
| 0x05-0xFF | reserved | 保留 |

### 11.2 compression

| 值 | 名称 | 说明 |
|----|------|------|
| 0x00 | NONE | 不压缩 |
| 0x01 | ZLIB | 通用压缩 |
| 0x02 | ZSTD | 高压缩比 + 快速解压 |
| 0x03 | LZ4 | 极速压缩/解压 |
| 0x04-0xFF | reserved | 保留 |

### 11.3 flags (全局)

| Bit | 名称 | 说明 |
|-----|------|------|
| 0 | ENCRYPTED | 文件已加密 |
| 1 | SIGNED | 文件已签名（预留） |
| 2-15 | reserved | 保留 |

### 11.4 group_type（v2 新增）

| 值 | 名称 | 说明 |
|----|------|------|
| 0x00 | GENERIC | 通用分组 |
| 0x01 | VIDEO_SUBTITLE | 视频+字幕 |
| 0x02 | DOCUMENT_SET | 文档集合 |
| 0x03 | MEDIA_ALBUM | 媒体专辑 |
| 0x04 | COURSE | 课程 |
| 0x05 | MEETING | 会议 |

### 11.5 relation_type — 组间关系（v2 新增）

| 值 | 名称 | 说明 |
|----|------|------|
| 0x00 | SEQUEL | 顺序/续集 |
| 0x01 | RELATED | 语义关联 |
| 0x02 | DEPENDS_ON | 依赖 |
| 0x03 | VARIANT | 变体/版本 |
| 0x04 | REFERENCES | 引用 |

### 11.6 intra_relation_type — 组内关系（v2.2 新增）

| 值 | 名称 | 说明 |
|----|------|------|
| 0x00 | SUBTITLE_OF | 字幕属于视频 |
| 0x01 | ATTACHMENT_OF | 附件属于主体 |
| 0x02 | TRANSCRIPT_OF | 转写属于音视频 |
| 0x03 | THUMBNAIL_OF | 缩略图属于原图 |
| 0x04 | ANNOTATION_OF | 批注属于文档 |
| 0x05 | CHAPTER_OF | 章节属于整体 |
| 0x06 | SUPPLEMENT_OF | 补充材料 |
| 0x07 | DERIVED_FROM | 派生自 |
| 0x08 | VERSION_OF | 另一版本 |
| 0xFF | CUSTOM | 自定义 |

### 11.7 encrypt_mode（v2.1 新增）

| 值 | 名称 | 说明 |
|----|------|------|
| 0x00 | NONE | 不加密 |
| 0x01 | FULL | 控制区 + 数据区全部加密 |
| 0x02 | METADATA_ONLY | 仅加密控制区 |
| 0x03 | DATA_ONLY | 仅加密数据区 |

### 11.8 kdf_type（v2.1 新增）

| 值 | 名称 | 说明 |
|----|------|------|
| 0x01 | SHA256_XOR | SHA-256 + XOR 流加密（零依赖） |
| 0x02 | PBKDF2_AES | PBKDF2 + AES-256-GCM（需 cryptography） |

---

## 12. MIME 类型参考

### 12.1 文档

| MIME 类型 | 扩展名 | 推荐压缩 |
|-----------|--------|----------|
| text/markdown | .md | zlib |
| text/plain | .txt | zlib |
| application/json | .json | zlib |
| application/pdf | .pdf | 无 |
| application/vnd.openxmlformats-officedocument.wordprocessingml.document | .docx | 无 |
| text/html | .html | zlib |
| text/csv | .csv | zlib |

### 12.2 图片

| MIME 类型 | 扩展名 | 推荐压缩 |
|-----------|--------|----------|
| image/jpeg | .jpg, .jpeg | 无 |
| image/png | .png | 无 |
| image/gif | .gif | 无 |
| image/webp | .webp | 无 |
| image/svg+xml | .svg | zlib |
| image/bmp | .bmp | zlib |
| image/tiff | .tiff | zlib |

### 12.3 音频

| MIME 类型 | 扩展名 | 推荐压缩 |
|-----------|--------|----------|
| audio/mpeg | .mp3 | 无 |
| audio/wav | .wav | zstd |
| audio/ogg | .ogg | 无 |
| audio/flac | .flac | 无 |
| audio/aac | .aac | 无 |
| audio/mp4 | .m4a | 无 |
| audio/x-wav | .wav | zstd |

### 12.4 视频（v2 新增）

| MIME 类型 | 扩展名 | 推荐压缩 |
|-----------|--------|----------|
| video/mp4 | .mp4, .m4v | 无 |
| video/x-matroska | .mkv | 无 |
| video/x-msvideo | .avi | 无 |
| video/quicktime | .mov | 无 |
| video/webm | .webm | 无 |
| video/x-flv | .flv | 无 |
| video/x-ms-wmv | .wmv | 无 |
| video/mp2t | .ts | 无 |

---

## 13. 元数据 JSON Schema

### 13.1 通用 Schema

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

### 13.2 文档扩展 Schema

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

### 13.3 图片扩展 Schema

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

### 13.4 音频扩展 Schema

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

## 14. 验证规则

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

## 15. 压缩细节

### 15.1 zlib (0x01)

- 默认压缩级别：6（平衡压缩比和速度）
- Python: `zlib.compress(data, level=6)`
- C++: `deflateInit2(&stream, 6, Z_DEFLATED, 15, 8, Z_DEFAULT_STRATEGY)`

### 15.2 zstd (0x02)

- 默认压缩级别：3（快速且高压缩比）
- Python: `zstd.compress(data, level=3)`
- C++: `ZSTD_compress(dst, dstSize, src, srcSize, 3)`

### 15.3 lz4 (0x03)

- 使用 LZ4 frame 格式
- Python: `lz4.frame.compress(data)`
- C++: `LZ4F_compressFrame(dst, dstSize, src, srcSize, NULL)`

---

## 16. CRC32 计算

- 算法：CRC-32/ISO-HDLC（即标准 CRC32，多项式 0x04C11DB7）
- 输入：原始文件数据（未压缩）
- Python: `binascii.crc32(data) & 0xFFFFFFFF`
- C++: 使用 `<zlib.h>` 中的 `crc32()` 函数

---

## 17. 条目 ID 生成

元数据中的 `parent_id` 和 `relationships[].target_id` 需要稳定的条目 ID。推荐方案：

- 使用文件名（不含扩展名）作为默认 ID，如 `notes.md` → `notes`
- 如有冲突，追加数字后缀：`notes_2`, `notes_3`
- 或使用内容的 SHA-256 前 8 字节作为 ID：`a3f2b1c8`

---

## 18. 实现检查清单

写入工具（v2）：
- [x] 正确写入 64 字节 Header，magic = "MCPK"，version = 2
- [x] 按小端序写入所有整数
- [x] 对每个文件判断压缩策略
- [x] 计算原始数据 CRC32
- [x] 按分组顺序写入 blob
- [x] 构建 Magic Index（每条目 48 字节）
- [x] 构建 Group Index（分组 + 标签 + 组内关系 + 组间关系）
- [x] 构建完整 TOC（50 字节固定部分 + 变长 name/mime/metadata）
- [x] 写入 Footer 并计算 footer_crc
- [x] 回写 Header 中的所有偏移和大小字段
- [x] [加密] 写入 Encryption Params 区（56 或 76 字节）
- [x] [加密] 控制区和数据区加密

读取工具（v2）：
- [x] 验证 Header magic 和 version
- [x] 从 Footer 或 Header 定位 TOC
- [x] 根据 version 分派 v1/v2 解析路径
- [x] 解析 Magic Index（快速概览文件类型）
- [x] 解析 Group Index（分组 + 标签 + 关系）
- [x] 解析所有 TOC Entry
- [x] 按需提取 blob 并解压
- [x] 校验 CRC32
- [x] 防范路径穿越攻击
- [x] [加密] 读取 Encryption Params，验证密码
- [x] [加密] AES-GCM 或 XOR 解密
