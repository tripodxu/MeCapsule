# MCPK v2 优化编码方案

**MeCapsule Package (MCPK) v2.0 — 编码优化设计**

> 在 v1.0 基础上引入三大核心优化：Magic 码聚合索引、分组存储、组间关系图。同时新增 VIDEO 条目类型，为加密场景提供结构性支撑。

---

## 1. 设计动机

### 1.1 v1.0 的不足

v1.0 的布局是 `Header → Blob0 → Blob1 → ... → BlobN → TOC → Footer`，存在以下问题：

| 问题 | 说明 |
|------|------|
| Magic 码分散 | 每个文件的 magic 码（文件签名）藏在各自的 blob 中，无法快速获取全局文件类型概览 |
| 无分组概念 | 相关文件（如视频+字幕、文档+附件）在物理上无关联，只能靠 metadata.relationships 逻辑关联 |
| 元数据分散 | 控制信息（TOC）与数据（Blob）完全分离，加密时需要分别处理 |
| 缺少 VIDEO | 知识管理场景中视频是重要内容载体 |

### 1.2 v2.0 的优化思路

```
核心理念：将「标识信息」和「控制信息」集中到文件首尾，与「数据区」形成清晰分离。

好处：
  1. 快速扫描：只读头部即可获取所有文件类型、分组关系
  2. 加密友好：控制区域可整体加密，数据区域可按需加密
  3. 流式处理：按分组顺序写入/读取相关文件，减少随机跳转
  4. 关系显式化：分组和组间关系在格式层面原生支持
```

---

## 2. 文件整体布局（v2）

```
┌──────────────────────────────────────────────────────┐
│                FILE HEADER (64 B, 固定)                │  偏移 0
├──────────────────────────────────────────────────────┤
│                                                      │
│            MAGIC INDEX TABLE (变长)                    │  偏移 64
│            聚合所有条目的 magic 码 + 基础类型信息        │
│                                                      │
├──────────────────────────────────────────────────────┤
│                                                      │
│            DATA SECTION (变长)                         │
│            按分组组织的 Blob 数据                       │
│                                                      │
│   ┌─ Group 0 ─────────────────────────┐              │
│   │  ┌──────────┐  ┌──────────┐      │              │
│   │  │ Blob 0   │  │ Blob 1   │      │              │
│   │  │(video1)  │  │(txt1.srt)│      │              │
│   │  └──────────┘  └──────────┘      │              │
│   └───────────────────────────────────┘              │
│   ┌─ Group 1 ─────────────────────────┐              │
│   │  ┌──────────┐  ┌──────────┐      │              │
│   │  │ Blob 2   │  │ Blob 3   │      │              │
│   │  │(video2)  │  │(txt2.srt)│      │              │
│   │  └──────────┘  └──────────┘      │              │
│   └───────────────────────────────────┘              │
│   ┌─ Ungrouped ───────────────────────┐              │
│   │  ┌──────────┐  ┌──────────┐      │              │
│   │  │ Blob 4   │  │ Blob 5   │      │              │
│   │  └──────────┘  └──────────┘      │              │
│   └───────────────────────────────────┘              │
│                                                      │
├──────────────────────────────────────────────────────┤
│            GROUP INDEX (变长)                          │
│            分组元数据 + 组间关系图                      │
│                                                      │
├──────────────────────────────────────────────────────┤
│            TOC (变长)                                  │
│            完整的条目索引 + 元数据（与 v1 兼容）         │
│                                                      │
├──────────────────────────────────────────────────────┤
│            FOOTER (16 B, 固定)                         │  偏移 filesize-16
└──────────────────────────────────────────────────────┘
```

**写入顺序：** Header → Magic Index → Blob Group 0 → Blob Group 1 → ... → Ungrouped Blobs → Group Index → TOC → Footer

**读取顺序：** Footer → Header → Magic Index（快速概览）→ TOC（完整索引）→ 按需读取 Blob

---

## 3. 各段详细设计

### 3.1 File Header（64 字节, 固定）

在 v1 基础上扩展，利用 reserved 字段：

```
偏移   大小   类型          字段名              说明
────────────────────────────────────────────────────────────────
0x00   4B    char[4]       magic               "MCPK"
0x04   2B    uint16_le     version             0x0002 (v2)
0x06   2B    uint16_le     flags               全局标志位
0x08   8B    uint64_le     created_at          创建时间戳 (Unix ms)
0x10   8B    uint64_le     magic_index_offset  Magic Index 起始偏移
0x18   8B    uint64_le     magic_index_size    Magic Index 字节数
0x20   4B    uint32_le     entry_count         条目总数
0x24   4B    uint32_le     group_count         分组总数（v2 新增）
0x28   8B    uint64_le     group_index_offset  Group Index 起始偏移（v2 新增）
0x30   8B    uint64_le     group_index_size    Group Index 字节数（v2 新增）
0x38   8B    uint64_le     toc_offset          TOC 起始偏移（从 reserved 借用）
────────────────────────────────────────────────────────────────
        64B 总计
```

**与 v1 的变化：**

| 字段 | v1 | v2.0 设计 | v2.2 实际 | 说明 |
|------|----|----|----|------|
| version | 1 | 2 | 2 | 版本号升级 |
| offset 0x08 | created_at | created_at | packed_at | 改为容器级打包时间 |
| offset 0x10 | toc_offset | magic_index_offset | ep_offset | 实际存储 Encryption Params 偏移 |
| offset 0x18 | toc_size | magic_index_size | ep_size | 实际存储 Encryption Params 大小 |
| offset 0x24 | data_section_size 低4B | group_count | group_count | 语义变更 |
| offset 0x28-0x2F | reserved | group_index_offset + size | group_index_offset + size | 新增 |
| offset 0x38-0x3F | reserved | toc_offset | toc_offset | 新增 |

> **注意：** v2.2 实际实现中，offset 0x10/0x18 存储的是 Encryption Params 区的位置（加密文件）或 0（非加密文件）。Magic Index 紧接 Header + EP 之后，偏移 = 64 + ep_size。

> **兼容性说明：** v2 的 Header 布局与 v1 不兼容。读取工具通过 version 字段区分版本。

---

### 3.2 Magic Index Table（新增）

紧接 Header 之后，聚合所有条目的文件签名（magic 码）和基础分类信息。

#### 3.2.1 Magic Index 头部

```
偏移(相对)  大小   类型          字段名          说明
────────────────────────────────────────────────────────────────
+0x00      4B    char[4]       index_magic     "MGIX"
+0x04      4B    uint32_le     entry_count     条目总数
+0x08      4B    uint32_le     index_size      本段总字节数
────────────────────────────────────────────────────────────────
           12B 固定头部
```

#### 3.2.2 Magic Entry（每条目固定 48 字节）

```
偏移(相对)  大小   类型          字段名          说明
────────────────────────────────────────────────────────────────
+0x00      4B    uint32_le     entry_id        条目序号 (0-based)
+0x04      1B    uint8_t       entry_type      条目类型 (0x01~0x05)
+0x05      1B    uint8_t       group_id        所属分组 ID (0xFF=无分组)
+0x06      2B    uint16_le     magic_len       magic 码实际字节数
+0x08      32B   bytes         magic_bytes     文件签名（最多 32 字节）
+0x28      2B    uint16_le     name_len        文件名字节数
+0x2A      6B    bytes         reserved        保留
────────────────────────────────────────────────────────────────
           48B 固定
```

**字段说明：**

- `entry_id`：与 TOC 中的条目顺序一一对应
- `entry_type`：快速分类（不需要解析 TOC）
- `group_id`：该条目所属的分组（0xFF 表示不属于任何分组）
- `magic_bytes`：文件前 32 字节签名。不足 32 字节时右补 0x00

**常见文件 magic 码参考：**

| 文件类型 | Magic (hex) | 长度 |
|----------|-------------|------|
| MP4 | `00 00 00 xx 66 74 79 70` | 8 |
| MKV | `1A 45 DF A3` | 4 |
| AVI | `52 49 46 46` | 4 |
| MP3 (ID3) | `49 44 33` | 3 |
| MP3 (frame sync) | `FF FB` | 2 |
| JPEG | `FF D8 FF` | 3 |
| PNG | `89 50 4E 47 0D 0A 1A 0A` | 8 |
| PDF | `25 50 44 46` | 4 |
| ZIP/DOCX | `50 4B 03 04` | 4 |
| WAV | `52 49 46 46` | 4 |
| FLAC | `66 4C 61 43` | 4 |
| OGG | `4F 67 67 53` | 4 |
| WebP | `52 49 46 46` (需检查 offset 8 = "WEBP") | 12 |
| GIF | `47 49 46 38` | 4 |
| BMP | `42 4D` | 2 |
| SVG | `3C 3F 78 6D 6C` (XML声明) 或 `3C 73 76 67` | 4+ |

**设计决策：**

- 每条目 48 字节，100 个文件也仅 4.8 KB，开销极小
- Magic Index 放在文件最前面，读取工具只需一次顺序读取即可获取全局文件类型
- 32 字节 magic 空间覆盖所有主流格式的文件签名
- 加密场景下，Magic Index 可整体加密，隐藏文件类型信息

---

### 3.3 分组存储（Data Section）

数据区按分组（Group）组织，相关文件的 blob 物理上相邻存放。

#### 3.3.1 分组规则

```
分组由用户在打包时显式指定，或由工具自动推断：

自动推断规则：
  - 同名不同扩展名的文件归为一组（如 video1.mp4 + video1.srt）
  - 同目录下的文件可选归为一组
  - metadata.relationships 中有 "transcript"/"attachment" 关系的文件归为一组

显式指定：
  - writer.add_group("meeting_2026", files=[video_path, subtitle_path, notes_path])
```

#### 3.3.2 物理排列

```
Data Section 内部布局：

[Group 0 Blobs]  ← group_id=0 的所有条目 blob，按添加顺序紧密排列
[Group 1 Blobs]  ← group_id=1 的所有条目 blob
...
[Group N Blobs]  ← group_id=N 的所有条目 blob
[Ungrouped Blobs] ← group_id=0xFF 的条目 blob
```

同一分组内的 blob 物理相邻，有利于：
- **顺序读取**：读取一个分组的所有文件只需一次顺序扫描
- **流式解密**：同一分组可使用同一加密密钥/nonce 前缀
- **局部性**：相关文件在磁盘上相邻，减少 I/O 寻道

> **注意：** blob 的绝对偏移仍然记录在 TOC 中（与 v1 一致），因此随机访问不受影响。分组只是一种物理排列优化。

---

### 3.4 Group Index（新增）

位于数据区之后、TOC 之前，记录分组元数据和组间关系。

#### 3.4.1 Group Index 头部

```
偏移(相对)  大小   类型          字段名          说明
────────────────────────────────────────────────────────────────
+0x00      4B    char[4]       index_magic     "GRPX"
+0x04      4B    uint32_le     group_count     分组总数
+0x08      4B    uint32_le     relation_count  关系总数
+0x0C      4B    uint32_le     index_size      本段总字节数
────────────────────────────────────────────────────────────────
           16B 固定头部
```

#### 3.4.2 Group Entry（每个分组变长）

```
偏移(相对)  大小     类型          字段名          说明
────────────────────────────────────────────────────────────────
+0x00      1B      uint8_t       group_id        分组 ID (0-based)
+0x01      1B      uint8_t       entry_count     组内条目数
+0x02      2B      uint16_le     group_type      分组类型（见下表）
+0x04      2B      uint16_le     name_len        分组名称字节数
+0x06      变长    char[]        group_name      UTF-8 分组名称
+...       2B      uint16_le     meta_len        分组元数据 JSON 字节数
+...       变长    char[]        group_metadata  分组级元数据 JSON（可选）
────────────────────────────────────────────────────────────────
```

**group_type 枚举：**

| 值 | 名称 | 说明 |
|----|------|------|
| 0x00 | GENERIC | 通用分组 |
| 0x01 | VIDEO_SUBTITLE | 视频+字幕 |
| 0x02 | DOCUMENT_SET | 文档集合（如报告+数据+图表） |
| 0x03 | MEDIA_ALBUM | 媒体专辑（如相册、音乐集） |
| 0x04 | COURSE | 课程（视频+讲义+作业） |
| 0x05 | MEETING | 会议（录音+纪要+附件） |
| 0x06-0xFF | reserved | 保留 |

#### 3.4.3 Group Relation（组间关系，变长）

```
偏移(相对)  大小     类型          字段名          说明
────────────────────────────────────────────────────────────────
+0x00      1B      uint8_t       source_group    源分组 ID
+0x01      1B      uint8_t       target_group    目标分组 ID
+0x02      2B      uint16_le     relation_type   关系类型
+0x04      2B      uint16_le     desc_len        描述字节数
+0x06      变长    char[]        description     UTF-8 描述（可选）
────────────────────────────────────────────────────────────────
```

**relation_type 枚举：**

| 值 | 名称 | 说明 | 示例 |
|----|------|------|------|
| 0x00 | SEQUEL | 顺序/续集 | 课程第1讲 → 第2讲 |
| 0x01 | RELATED | 语义关联 | 两个相关会议 |
| 0x02 | DEPENDS_ON | 依赖关系 | 实验报告 → 原始数据 |
| 0x03 | VARIANT | 变体/版本 | 中文版 → 英文版 |
| 0x04 | REFERENCES | 引用 | 论文 → 参考文献集 |
| 0x05-0xFF | reserved | 保留 | |

**示例：分组元数据 JSON**

```json
{
  "title": "机器学习课程第3讲",
  "description": "神经网络基础",
  "created": "2026-04-15",
  "tags": ["课程", "ML", "深度学习"],
  "custom": {
    "instructor": "张教授",
    "week": 3
  }
}
```

---

### 3.5 TOC（与 v1 兼容）

TOC 格式与 v1 完全一致，每个 TocEntry 包含完整的条目索引和元数据。

唯一的扩展：在 TocEntry 的 reserved[2] 字段中，可选存储 group_id：

```
reserved[0] = group_id (0xFF = 无分组)
reserved[1] = 保留
```

这使得读取工具可以仅通过 TOC 即可知道每个条目的分组归属，无需读取 Magic Index。

---

### 3.6 Footer（16 字节, 固定）

与 v1 完全一致，无需修改。

---

## 4. 新增 VIDEO 类型

### 4.1 EntryType 扩展

| 值 | 名称 | 说明 |
|----|------|------|
| 0x01 | DOCUMENT | 文档 |
| 0x02 | IMAGE | 图片 |
| 0x03 | AUDIO | 音频 |
| 0x04 | VIDEO | 视频（v2 新增） |

### 4.2 视频扩展名映射

| 扩展名 | MIME 类型 | 推荐压缩 |
|--------|-----------|----------|
| .mp4 | video/mp4 | NONE |
| .mkv | video/x-matroska | NONE |
| .avi | video/x-msvideo | NONE |
| .mov | video/quicktime | NONE |
| .webm | video/webm | NONE |
| .flv | video/x-flv | NONE |
| .wmv | video/x-ms-wmv | NONE |
| .ts | video/mp2t | NONE |
| .m4v | video/mp4 | NONE |

> 视频文件几乎总是已压缩格式，因此压缩策略为 NONE。

### 4.3 视频元数据 Schema

```json
{
  "title": "机器学习课程第3讲",
  "duration_ms": 3600000,
  "width": 1920,
  "height": 1080,
  "fps": 30.0,
  "codec": "h264",
  "bitrate_kbps": 5000,
  "tags": ["课程", "ML"],
  "parent_id": "root",
  "custom": {}
}
```

---

## 5. 加密架构（v2 预备设计）

> **注意：** 本节为 v2.0 阶段的预备设计。实际加密实现见 [MCPK-v2.1-Design.md](MCPK-v2.1-Design.md)（XOR）和 [MCPK-v2.2-Design.md](MCPK-v2.2-Design.md)（AES-GCM）。主要差异：
> - 实际使用 Encryption Params 区（56/76 字节）而非 Header flags
> - 实际密钥派生：XOR 用 SHA-256 混合，AES 用 PBKDF2 + HKDF
> - 实际加密模式：FULL / METADATA_ONLY / DATA_ONLY（无 PER_GROUP/PER_ENTRY）

v2 的布局天然适合分层加密：

### 5.1 加密区域划分

```
┌────────────────────────────┐
│  File Header (64 B)        │  明文（必需，用于识别文件格式和定位加密区域）
├────────────────────────────┤
│  Magic Index               │  ← 可整体加密（隐藏文件类型信息）
├────────────────────────────┤
│  Data Section (Blobs)      │  ← 按分组或按条目加密
├────────────────────────────┤
│  Group Index               │  ← 可整体加密（隐藏分组结构）
├────────────────────────────┤
│  TOC                       │  ← 可整体加密（隐藏文件名、元数据）
├────────────────────────────┤
│  Footer (16 B)             │  明文（必需，用于从尾部定位）
└────────────────────────────┘
```

### 5.2 加密模式

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| FULL | Magic Index + Data + Group Index + TOC 整体加密 | 最高安全 |
| METADATA_ONLY | 只加密 Magic Index + Group Index + TOC | 快速加密，隐藏结构 |
| PER_GROUP | 每个分组使用独立密钥 | 分组访问控制 |
| PER_ENTRY | 每个 blob 独立加密（v1 方案） | 精细控制 |

### 5.3 密钥派生

```
用户密码 → PBKDF2-SHA256 (iterations=600000) → 256-bit 主密钥
主密钥 → HKDF-SHA256 → 派生子密钥：
  - metadata_key: 加密控制区域
  - data_key_0: 加密 Group 0
  - data_key_1: 加密 Group 1
  - ...
```

---

## 6. 完整文件示例

一个包含 2 个分组（视频+字幕 × 2）和 1 个独立文档的 MCPK v2 文件：

```
条目:
  Group 0 "机器学习第1讲":
    - lecture1.mp4  (50MB, VIDEO, 无压缩)
    - lecture1.srt  (12KB, DOCUMENT, zlib 压缩 → 4KB)
  Group 1 "机器学习第2讲":
    - lecture2.mp4  (48MB, VIDEO, 无压缩)
    - lecture2.srt  (10KB, DOCUMENT, zlib 压缩 → 3KB)
  Ungrouped:
    - notes.md      (5KB, DOCUMENT, zlib 压缩 → 2KB)

组间关系:
  Group 0 → Group 1: SEQUEL (第1讲 → 第2讲)
```

### 6.1 二进制结构

```
[File Header: 64 bytes]
  4D 43 50 4B              magic = "MCPK"
  02 00                    version = 2
  00 00                    flags = 0
  [8B created_at]
  40 00 00 00 00 00 00 00  magic_index_offset = 64
  [8B magic_index_size]
  05 00 00 00              entry_count = 5
  02 00 00 00              group_count = 2
  [8B group_index_offset]
  [8B group_index_size]
  [8B toc_offset]

[Magic Index: ~252 bytes]
  4D 47 49 58              index_magic = "MGIX"
  05 00 00 00              entry_count = 5
  [4B index_size]

  Entry 0: lecture1.mp4
    00 00 00 00            entry_id = 0
    04                     entry_type = VIDEO
    00                     group_id = 0
    08 00                  magic_len = 8
    [32B magic_bytes]      00 00 00 xx 66 74 79 70 ...
    [2B name_len]
    [6B reserved]

  Entry 1: lecture1.srt
    01 00 00 00            entry_id = 1
    01                     entry_type = DOCUMENT
    00                     group_id = 0
    [magic ...]

  Entry 2: lecture2.mp4    group_id = 1
  Entry 3: lecture2.srt    group_id = 1
  Entry 4: notes.md        group_id = 0xFF (无分组)

[Data Section]
  [Group 0 Blobs]
    [lecture1.mp4 blob: ~50MB]
    [lecture1.srt blob: ~4KB (zlib)]
  [Group 1 Blobs]
    [lecture2.mp4 blob: ~48MB]
    [lecture2.srt blob: ~3KB (zlib)]
  [Ungrouped Blobs]
    [notes.md blob: ~2KB (zlib)]

[Group Index: ~120 bytes]
  47 52 50 58              index_magic = "GRPX"
  02 00 00 00              group_count = 2
  01 00 00 00              relation_count = 1
  [4B index_size]

  Group 0:
    00                     group_id = 0
    02                     entry_count = 2
    01 00                  group_type = VIDEO_SUBTITLE
    [name: "机器学习第1讲"]
    [metadata JSON]

  Group 1:
    01                     group_id = 1
    02                     entry_count = 2
    01 00                  group_type = VIDEO_SUBTITLE
    [name: "机器学习第2讲"]
    [metadata JSON]

  Relation 0:
    00                     source_group = 0
    01                     target_group = 1
    00 00                  relation_type = SEQUEL
    [description: "课程顺序"]

[TOC: ~300 bytes]
  (与 v1 格式完全一致，reserved[0] 存储 group_id)

[Footer: 16 bytes]
  4D 43 50 4B
  [8B toc_offset]
  [4B footer_crc]
```

---

## 7. 写入流程（v2）

```
1. 创建文件，写入 Header（64B 占位）
2. 记录 magic_index_offset = 64
3. 收集所有待打包文件的信息（magic 码、类型、分组）
4. 写入 Magic Index
5. 按分组顺序写入 Blob：
   a. 对每个分组：
      - 对组内每个文件：
        i.   读取原始数据
        ii.  计算 CRC32
        iii. 压缩（如需要）
        iv.  写入 blob，记录 blob_offset
   b. 写入不属于任何分组的文件 blob
6. 记录 group_index_offset = 当前偏移
7. 写入 Group Index
8. 记录 toc_offset = 当前偏移
9. 写入 TOC
10. 写入 Footer
11. 回写 Header 中的所有偏移和大小字段
```

---

## 8. 读取流程（v2）

```
1. 读取 Footer（文件末尾 16B）→ 获取 toc_offset
2. 读取 Header（前 64B）→ 验证 magic 和 version
3. 如果只需快速概览：
   - 读取 Magic Index → 获取所有文件类型、分组信息
   - 可选：读取 Group Index → 获取分组关系
4. 如果需要完整信息：
   - 跳转到 toc_offset，解析所有 TocEntry
5. 提取文件：
   - 从 TOC 获取 blob_offset → 跳转 → 读取 → 解压 → 校验 CRC32
6. 提取整个分组：
   - 从 Group Index 获取组内条目 → 按 blob_offset 顺序读取
```

---

## 9. 与 v1 的兼容性

| 方面 | 策略 |
|------|------|
| 读取工具 | 必须检查 version 字段。version=1 用 v1 解析，version=2 用 v2 解析 |
| 写入工具 | 默认写入 v2。提供 `--format v1` 选项生成 v1 兼容文件 |
| TOC 格式 | v2 的 TOC 与 v1 完全一致，确保核心数据结构兼容 |
| Footer 格式 | v2 的 Footer 与 v1 完全一致 |
| 降级 | v2 文件可降级为 v1（丢弃 Magic Index 和 Group Index，重新排列 blob） |

---

## 10. 性能分析

### 10.1 空间开销

| 组件 | 大小 | 说明 |
|------|------|------|
| Magic Index | 12 + 48 × N 字节 | N=100 时约 4.8 KB |
| Group Index | 16 + ~60 × G + ~40 × R 字节 | G=10 组、R=5 关系时约 0.8 KB |
| 总开销 | ~6 KB (100 文件, 10 组) | 相比数据区可忽略 |

### 10.2 时间收益

| 操作 | v1 | v2 | 提升 |
|------|----|----|------|
| 获取文件类型概览 | 需解析完整 TOC | 只读 Magic Index (~5KB) | 100x+ |
| 提取相关文件组 | 随机跳转 | 顺序读取 | I/O 局部性提升 |
| 加密元数据 | 需分别处理 TOC | Magic Index + Group Index + TOC 整体处理 | 简化流程 |

---

## 11. 实现优先级

| 阶段 | 内容 | 优先级 |
|------|------|--------|
| Phase 1 | VIDEO 类型 + 分组存储（Group 概念） | 高 |
| Phase 2 | Magic Index Table | 高 |
| Phase 3 | Group Index + 组间关系 | 中 |
| Phase 4 | 加密支持（利用 v2 布局） | 低（后续版本） |

---

## 12. 向后迁移路径

从 v1 迁移到 v2 的步骤：

```
1. 更新 Header: version 1→2, 重新分配 reserved 字段
2. 在 Header 后插入 Magic Index
3. 按分组重排 Blob（可选，也可保持原有顺序，group_id=0xFF）
4. 在 TOC 前插入 Group Index
5. TOC 的 reserved[0] 填入 group_id
6. Footer 不变
```

提供迁移工具：
```bash
python -m mcpk upgrade archive_v1.mcpk -o archive_v2.mcpk
```
