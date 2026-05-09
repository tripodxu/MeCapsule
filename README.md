# MCPK — MeCapsule Package

个人知识管理专用的自定义二进制容器格式。将文档、图片、音频、视频及其元数据打包为单一 `.mcpk` 文件。

## 特性

- 单一文件归档，内建元数据、标签、条目关系
- **Magic Index** — 文件头聚合所有文件签名（magic 码），快速识别类型
- **分组存储** — 相关文件（视频+字幕、课程+讲义）物理相邻存放
- **Group Index** — 分组元数据 + 标签 + 组内关系 + 组间关系图
- **完整时间戳** — created_at（源文件创建时间）、modified_at（修改时间）、packed_at（打包时间）
- **双加密模式** — AES-256-GCM 认证加密（高强度）/ XOR 流加密（零依赖），支持全量/仅元数据/仅数据三种模式
- **分组标签** — 每个分组可带多个字符串标签，自动去重
- **组内关系** — 同组条目间的语义关系（字幕、附件、转写、缩略图等 10 种类型）
- **按文件夹打包** — `import_folder()` 自动创建同名分组
- **JSON 索引打包** — `load_index()` 从 JSON 配置批量打包，缺失文件跳过警告
- 不加密模式完全兼容，加密是可选的
- 每条目独立压缩（自动判断：文本用 zlib，已压缩格式跳过）
- CRC32 完整性校验
- 流式写入，随机读取
- 支持文档、图片、音频、视频四大类，自动识别扩展名和 MIME 类型
- v1/v2 向后兼容
- Python API + 命令行工具

## 快速开始

### Python API

```python
from mcpk import MCPKWriter, MCPKReader, GroupType, RelationType, IntraRelationType

# ── 基本打包（不加密） ──
with MCPKWriter("my-capsule.mcpk") as w:
    w.add_file("report.pdf", metadata={"title": "季度报告"})
    w.add_file("photo.jpg")
    w.add_directory("./notes/", prefix="notes/")

# ── 分组打包 + 标签 + 组内关系 ──
with MCPKWriter("course.mcpk") as w:
    g1 = w.create_group("第1讲", GroupType.COURSE, tags=["ML", "入门"])
    w.add_file("lecture1.mp4", group=g1)
    w.add_file("lecture1.srt", group=g1)
    w.add_intra_relation("第1讲", source="lecture1.srt", target="lecture1.mp4",
                          relation_type=IntraRelationType.SUBTITLE_OF)

    g2 = w.create_group("第2讲", GroupType.COURSE, tags=["ML", "CNN"])
    w.add_file("lecture2.mp4", group=g2)

    # 组间关系
    w.add_relation("第1讲", "第2讲", RelationType.SEQUEL, description="课程顺序")

# ── 按文件夹打包 ──
with MCPKWriter("folders.mcpk") as w:
    w.import_folder("./课程资料/", tags=["课程"])
    w.import_folder("./作业/", tags=["作业"])
    w.add_relation("课程资料", "作业", RelationType.REFERENCES)

# ── JSON 索引打包 ──
with MCPKWriter("indexed.mcpk") as w:
    result = w.load_index("index.json", base_dir="./project/")
    print(f"加载 {result['loaded']} 文件, 跳过 {len(result['skipped'])} 文件")

# ── AES-256-GCM 加密打包 ──
with MCPKWriter("secret.mcpk", password="mypassword") as w:
    w.add_file("private.md")

# ── XOR 加密打包（零依赖） ──
with MCPKWriter("compat.mcpk", password="mypassword", encryption="xor") as w:
    w.add_file("video.mp4")

# ── 读取 ──
with MCPKReader("my-capsule.mcpk") as r:
    print(r.version)  # 2
    for entry in r.entries:
        print(entry.name, entry.mime_type, entry.original_size)
    data = r.extract("report.pdf")
    r.extract_all("./output/")
    errors = r.verify()

    # 分组 + 标签 + 组内关系
    for group in r.groups:
        print(f"分组: {group.name}, 标签: {group.tags}, 条目数: {len(group.entry_ids)}")
        for ir in group.intra_relations:
            src = r.entries[ir.source_entry].name
            tgt = r.entries[ir.target_entry].name
            print(f"  组内关系: {src} -> {tgt}")

    # 组间关系
    for rel in r.relations:
        print(f"关系: {rel.source_group} -> {rel.target_group}")

    # 按分组提取
    r.extract_group("第1讲", "./output/")

    # Magic Index
    for me in r.magic_entries:
        entry = r.entries[me.entry_id]
        print(f"  {entry.name}: magic={me.magic_bytes[:4].hex()}")

    # 时间戳
    for entry in r.entries:
        print(f"  {entry.name}: 创建={entry.time_info()['created']}, "
              f"修改={entry.time_info()['modified']}")
    print(f"  容器打包时间: {r.header.packed_at_iso()}")

# ── 读取加密文件 ──
with MCPKReader("secret.mcpk", password="mypassword") as r:
    data = r.extract("private.md")
    print(r.is_encrypted)  # True
    print(r.encryption_params.kdf_type)  # KdfType.PBKDF2_AES
```

### 命令行

```bash
# 打包（不加密）
python -m mcpk pack file1.md file2.jpg ./audio/ -o archive.mcpk

# 打包并归入分组
python -m mcpk pack video.mp4 subtitle.srt -o course.mcpk --group "第1讲"

# 按文件夹自动分组
python -m mcpk pack folder_A/ folder_B/ -o output.mcpk --auto-group

# JSON 索引打包
python -m mcpk pack --index index.json --base-dir ./project/ -o output.mcpk

# AES-GCM 加密打包（默认）
python -m mcpk pack private.md -o secret.mcpk -p "mypassword"

# XOR 加密打包（零依赖）
python -m mcpk pack video.mp4 -o compat.mcpk -p "mypassword" --encryption xor

# 仅加密元数据
python -m mcpk pack video.mp4 -o obscured.mcpk -p "mypassword" --encrypt-mode metadata_only

# 列出条目
python -m mcpk list archive.mcpk
python -m mcpk list archive.mcpk --type video
python -m mcpk list archive.mcpk --json

# 列出分组、标签和关系
python -m mcpk groups archive.mcpk

# 提取
python -m mcpk extract archive.mcpk -o ./output/
python -m mcpk extract archive.mcpk -n photo.jpg -o ./output/
python -m mcpk extract archive.mcpk -g "第1讲" -o ./output/

# 提取加密文件
python -m mcpk extract secret.mcpk -o ./output/ -p "mypassword"

# 检查详情
python -m mcpk inspect archive.mcpk

# 验证完整性
python -m mcpk verify archive.mcpk
```

## 文件格式概览（v2）

```
┌────────────────────────────────┐
│  File Header (64 B, 固定)       │  magic="MCPK", version=2, flags,
│                                │  packed_at, 各段偏移/大小
│                                │  group_index_size 字段存储 mi_encrypted_size
├────────────────────────────────┤
│  Encryption Params (56/76 B)   │  [仅加密文件] salt + 密钥验证 hash
│    ENC0 + kdf + mode + salt    │  XOR: 56B / AES-GCM: 76B
├────────────────────────────────┤
│  Magic Index (变长)             │  聚合所有文件签名 + 类型 + 分组归属
│    MGIX + MagicEntry × N       │  每条目 48 字节，含 32B magic 码
│                                │  [加密时] AES-GCM 或 XOR
├────────────────────────────────┤
│  Data Section (变长)            │  按分组排列的 Blob 数据
│    [Group 0 Blobs]             │    同组文件物理相邻
│    [Group 1 Blobs]             │    → 顺序读取效率高
│    ...                         │    → 便于分组级加密
│    [Ungrouped Blobs]           │  [加密时] salt(16B) + AES-GCM/XOR
├────────────────────────────────┤
│  Group Index (变长)             │  分组元数据 + 标签 + 组内关系 + 组间关系
│    GRPX + GroupEntry × G       │  分组名、类型、标签、组内条目 ID
│    + GroupRelation × R         │  SEQUEL / RELATED / DEPENDS_ON ...
│                                │  [加密时] AES-GCM 或 XOR
├────────────────────────────────┤
│  TOC (变长)                     │  每条目: type, compression, crc32,
│                                │  created_at, modified_at, name, mime,
│                                │  metadata, group_id
│                                │  [加密时] AES-GCM 或 XOR
├────────────────────────────────┤
│  Footer (16 B, 固定)            │  magic + toc_offset + crc32
└────────────────────────────────┘
```

详细二进制规范见 [MCPK-DataFormat.md](MCPK-DataFormat.md)，v2 设计方案见 [MCPK-v2-Design.md](MCPK-v2-Design.md)，v2.2 增强设计见 [MCPK-v2.2-Design.md](MCPK-v2.2-Design.md)。

## 加密架构

### AES-256-GCM（默认，需 cryptography）

```
用户密码 → PBKDF2-HMAC-SHA256 (600000 次迭代, salt=32B) → master_key
master_key → HKDF-SHA256 → control_key (加密控制区)
master_key → HKDF-SHA256 → data_key_base → blob_key_i (加密每条目)
```

- AES-256-GCM 认证加密，防篡改
- 每条目独立 nonce + tag
- `InvalidTag` 异常统一包装为 `MCPKError`

### XOR 流加密（零依赖）

```
password + salt → SHA-256 → master_key
master_key → SHA256(master_key + "ctrl") → control_key → XOR 加密控制区
master_key → SHA256(master_key + entry_id + entry_salt) → blob_key → XOR 加密单条目
```

### 加密模式

| 模式 | Magic Index | Blob 数据 | Group Index | TOC | 场景 |
|------|-------------|-----------|-------------|-----|------|
| FULL | 加密 | 加密 | 加密 | 加密 | 完全保护 |
| METADATA_ONLY | 加密 | 明文 | 加密 | 加密 | 隐藏结构，数据可快速访问 |
| DATA_ONLY | 明文 | 加密 | 明文 | 明文 | 仅保护内容 |

## 支持的文件类型

| 类型 | 扩展名 | entry_type | 压缩策略 |
|------|--------|------------|----------|
| 文档 | .md .txt .json .csv .html .xml .yaml | DOCUMENT | zlib |
| 文档 | .pdf .docx .doc .xlsx .pptx | DOCUMENT | 无（内部已压缩） |
| 字幕 | .srt .vtt .ass | DOCUMENT | zlib |
| 图片 | .jpg .png .gif .webp .svg .ico | IMAGE | 无 |
| 图片 | .bmp .tiff | IMAGE | zlib |
| 音频 | .mp3 .ogg .flac .aac .m4a .wma | AUDIO | 无 |
| 音频 | .wav | AUDIO | zstd（回退 zlib） |
| 视频 | .mp4 .mkv .avi .mov .webm .flv .wmv .ts .m4v | VIDEO | 无 |

## 分组类型

| 类型 | 说明 | 典型场景 |
|------|------|----------|
| GENERIC | 通用分组 | 任意文件集合 |
| VIDEO_SUBTITLE | 视频+字幕 | 电影/课程配字幕 |
| DOCUMENT_SET | 文档集合 | 报告+数据+图表 |
| MEDIA_ALBUM | 媒体专辑 | 相册、音乐集 |
| COURSE | 课程 | 视频+讲义+作业 |
| MEETING | 会议 | 录音+纪要+附件 |

## 组内关系类型

| 类型 | 说明 | 示例 |
|------|------|------|
| SUBTITLE_OF | 字幕属于视频 | lecture.srt → lecture.mp4 |
| ATTACHMENT_OF | 附件属于主体 | appendix.pdf → report.docx |
| TRANSCRIPT_OF | 转写属于音视频 | transcript.txt → meeting.mp3 |
| THUMBNAIL_OF | 缩略图属于原图 | thumb.jpg → photo.jpg |
| ANNOTATION_OF | 批注属于文档 | notes.md → slides.pdf |
| CHAPTER_OF | 章节属于整体 | chapter1.md → book.pdf |
| SUPPLEMENT_OF | 补充材料 | extra.pdf → main.pdf |
| DERIVED_FROM | 派生自 | extract.txt → scan.pdf |
| VERSION_OF | 另一版本 | v2.docx → v1.docx |
| CUSTOM | 自定义 | 用户自定义 |

## 组间关系类型

| 类型 | 说明 | 示例 |
|------|------|------|
| SEQUEL | 顺序/续集 | 第1讲 → 第2讲 |
| RELATED | 语义关联 | 两个相关会议 |
| DEPENDS_ON | 依赖 | 实验报告 → 原始数据 |
| VARIANT | 变体/版本 | 中文版 → 英文版 |
| REFERENCES | 引用 | 论文 → 参考文献集 |

## 项目结构

```
mcpk/
├── __init__.py      # 包入口，导出主要类和加密工具
├── __main__.py      # python -m mcpk 支持
├── constants.py     # 常量、枚举（EntryType/Compression/KdfType/IntraRelationType 等）
├── types.py         # FileHeader / TocEntry / MagicEntry / GroupEntry / IntraRelation / EncryptionParams
├── writer.py        # MCPKWriter（分组 + 标签 + 组内关系 + AES-GCM/XOR 加密 + import_folder + load_index）
├── reader.py        # MCPKReader（v1/v2 兼容 + AES-GCM/XOR 解密 + tags/intra_relations 解析）
└── cli.py           # 命令行（pack/list/groups/extract/inspect/verify，支持 --encryption/--auto-group/--index）
test_mcpk.py         # 集成测试（48 个用例）
MCPK-v2-Design.md    # v2 编码优化设计（Magic Index + 分组 + Group Index）
MCPK-v2.1-Design.md  # v2.1 增强设计（时间戳 + XOR 加密方案）
MCPK-v2.2-Design.md  # v2.2 增强设计（AES-GCM + 标签 + 组内关系 + 索引打包）
```

## 运行测试

```bash
# 清除旧缓存
rd /s /q mcpk\__pycache__

# 快速模式（小文件，约几秒）
python test_mcpk.py --quick

# 完整模式（含 medium 大文件）
python test_mcpk.py

# 大文件模式（50~100MB 视频，需要较多时间和磁盘）
python test_mcpk.py --large-only

# 仅运行某个测试
python test_mcpk.py --test test_21

# 手动指定大小预设
python test_mcpk.py --size xlarge
```

测试覆盖 48 个用例：基本 Roundtrip、分组存储、VIDEO 类型、Magic Index、压缩比、大批量条目、边界情况、v1 兼容、复杂分组场景、性能基准、Blob 排序、Inspect JSON、add_data API、重复打包一致性、压力测试、XOR 加密三种模式、不加密兼容性、时间戳验证、加密+分组组合、AES-GCM 三种模式、错误密码/篡改检测、XOR 向后兼容、分组标签、动态 Tag、组内关系、import_folder、JSON 索引打包、Tag 去重、全 IntraRelationType 覆盖。

## 性能参考

在普通 PC（SSD）上的实测数据：

| 操作 | 数据量 | 速度 |
|------|--------|------|
| 打包 | 114 MB (24 文件) | ~325 MB/s |
| 校验 | 114 MB | ~1060 MB/s |
| 提取最大文件 | 95 MB | ~1500 MB/s |
| 全量提取 | 114 MB | ~530 MB/s |

## 依赖

- Python 3.10+
- 标准库：`struct`, `zlib`, `json`, `binascii`, `hashlib`, `os`, `time`
- 可选：`cryptography`（AES-256-GCM 加密）、`zstd`（Zstandard 压缩）、`lz4`（LZ4 压缩）

```bash
pip install cryptography zstd lz4
```

`cryptography` 不安装时仍可使用 XOR 加密和所有非加密功能。

## 路线图

详见 [ROADMAP.md](ROADMAP.md)。
