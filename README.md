# MCPK — MeCapsule Package

个人知识管理专用的自定义二进制容器格式。将文档、图片、音频、视频及其元数据打包为单一 `.mcpk` 文件。

## 特性

- 单一文件归档，内建元数据、标签、条目关系
- **Magic Index** — 文件头聚合所有文件签名（magic 码），快速识别类型
- **分组存储** — 相关文件（视频+字幕、课程+讲义）物理相邻存放
- **Group Index** — 分组元数据 + 组间关系图（顺序、关联、依赖等）
- **完整时间戳** — created_at（源文件创建时间）、modified_at（修改时间）、packed_at（打包时间）
- **可选加密** — XOR 流加密，密码派生密钥，支持全量/仅元数据/仅数据三种模式
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
from mcpk import MCPKWriter, MCPKReader, GroupType, RelationType

# ── 基本打包（不加密） ──
with MCPKWriter("my-capsule.mcpk") as w:
    w.add_file("report.pdf", metadata={"title": "季度报告", "tags": ["工作", "Q1"]})
    w.add_file("photo.jpg")
    w.add_directory("./notes/", prefix="notes/")

# ── 分组打包（视频+字幕+讲义为一组） ──
with MCPKWriter("course.mcpk") as w:
    w.add_file("lecture1.mp4", group_name="第1讲")
    w.add_file("lecture1.srt", group_name="第1讲")
    w.add_file("slides1.md",  group_name="第1讲")

    w.add_file("lecture2.mp4", group_name="第2讲")
    w.add_file("lecture2.srt", group_name="第2讲")

    # 组间关系
    w.add_relation("第1讲", "第2讲", RelationType.SEQUEL, description="课程顺序")

# ── 显式分组 API ──
with MCPKWriter("meeting.mcpk") as w:
    g = w.create_group("周一例会", GroupType.MEETING,
                        metadata={"date": "2026-05-08"})
    w.add_file("recording.mp3", group=g)
    w.add_file("notes.md",      group=g)

# ── 读取 ──
with MCPKReader("my-capsule.mcpk") as r:
    # 版本检测
    print(r.version)  # 2

    # 列出条目
    for entry in r.entries:
        print(entry.name, entry.mime_type, entry.original_size)

    # 提取单个
    data = r.extract("report.pdf")

    # 提取全部
    r.extract_all("./output/")

    # 校验
    errors = r.verify()

    # v2: 分组信息
    for group in r.groups:
        print(f"分组: {group.name}, 条目数: {len(group.entry_ids)}")
    for rel in r.relations:
        print(f"关系: {rel.source_group} -> {rel.target_group}")

    # 按分组提取
    r.extract_group("第1讲", "./output/")

    # Magic Index（快速获取文件类型）
    for me in r.magic_entries:
        entry = r.entries[me.entry_id]
        print(f"  {entry.name}: magic={me.magic_bytes[:4].hex()}")

    # 时间戳
    for entry in r.entries:
        print(f"  {entry.name}: 创建={entry.time_info()['created']}, "
              f"修改={entry.time_info()['modified']}")
    print(f"  容器打包时间: {r.header.packed_at_iso()}")
```

### 加密打包（可选）

```python
# ── 全量加密 ──
with MCPKWriter("secret.mcpk", password="mypassword") as w:
    w.add_file("private.md")
    w.add_file("photo.jpg")

# ── 仅加密元数据（隐藏文件名/类型/分组，数据区明文） ──
with MCPKWriter("obscured.mcpk", password="mypassword",
                encrypt_mode="metadata_only") as w:
    w.add_file("video.mp4")

# ── 读取加密文件 ──
with MCPKReader("secret.mcpk", password="mypassword") as r:
    data = r.extract("private.md")
    print(r.is_encrypted)  # True
    print(r.encryption_params.encrypt_mode)  # EncryptionMode.FULL

# ── 密码错误 ──
try:
    with MCPKReader("secret.mcpk", password="wrong") as r:
        pass
except MCPKError as e:
    print(f"解密失败: {e}")  # "密码错误或文件已损坏"
```

### 命令行

```bash
# 打包（不加密）
python -m mcpk pack file1.md file2.jpg ./audio/ -o archive.mcpk

# 打包并归入分组
python -m mcpk pack video.mp4 subtitle.srt -o course.mcpk --group "第1讲"

# 加密打包
python -m mcpk pack private.md photo.jpg -o secret.mcpk -p "mypassword"

# 仅加密元数据
python -m mcpk pack video.mp4 -o obscured.mcpk -p "mypassword" --encrypt-mode metadata_only

# 列出条目（支持 video 类型）
python -m mcpk list archive.mcpk
python -m mcpk list archive.mcpk --type video
python -m mcpk list archive.mcpk --json

# 列出加密文件条目
python -m mcpk list secret.mcpk -p "mypassword"

# 列出分组和关系
python -m mcpk groups archive.mcpk

# 提取
python -m mcpk extract archive.mcpk -o ./output/
python -m mcpk extract archive.mcpk -n photo.jpg -o ./output/
python -m mcpk extract archive.mcpk -g "第1讲" -o ./output/   # 按分组提取

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
├────────────────────────────────┤
│  Encryption Params (56 B)      │  [仅加密文件] salt + 密钥验证 hash
│    ENC0 + kdf + mode + salt    │  明文存储，用于验证密码
├────────────────────────────────┤
│  Magic Index (变长)             │  聚合所有文件签名 + 类型 + 分组归属
│    MGIX + MagicEntry × N       │  每条目 48 字节，含 32B magic 码
│                                │  [加密时] XOR(control_key)
├────────────────────────────────┤
│  Data Section (变长)            │  按分组排列的 Blob 数据
│    [Group 0 Blobs]             │    同组文件物理相邻
│    [Group 1 Blobs]             │    → 顺序读取效率高
│    ...                         │    → 便于分组级加密
│    [Ungrouped Blobs]           │  [加密时] salt(16B) + XOR(blob_key)
├────────────────────────────────┤
│  Group Index (变长)             │  分组元数据 + 组间关系图
│    GRPX + GroupEntry × G       │  分组名、类型、组内条目 ID
│    + GroupRelation × R         │  SEQUEL / RELATED / DEPENDS_ON ...
│                                │  [加密时] XOR(control_key)
├────────────────────────────────┤
│  TOC (变长)                     │  每条目: type, compression, crc32,
│                                │  created_at, modified_at, name, mime,
│                                │  metadata, group_id
│                                │  [加密时] XOR(control_key)
├────────────────────────────────┤
│  Footer (16 B, 固定)            │  magic + toc_offset + crc32
└────────────────────────────────┘
```

**设计要点：**
- 控制信息（Magic Index + Group Index + TOC）集中在文件首尾，与数据区分离
- 每条目记录三个时间戳：created_at（源文件创建）、modified_at（源文件修改）、packed_at（打包时刻，存于 Header）
- 不加密时 Encryption Params 区不存在，文件布局与无加密完全一致
- 加密时 Header 的 flags.ENCRYPTED=1，读取工具据此判断是否需要密码
- 加密密钥派生链路（对齐 1apluse 风格）：`password + salt → SHA-256 → master_key → XOR 子密钥`
- v1 兼容：version=1 的文件按旧布局解析，version=2 按新布局

详细二进制规范见 [MCPK-DataFormat.md](MCPK-DataFormat.md)，v2 设计方案见 [MCPK-v2-Design.md](MCPK-v2-Design.md)。

## 加密架构

加密基于 1apluse 的 XOR 流加密体系，适配 MCPK v2 分层布局。

### 密钥派生

```
用户密码 + salt(128-bit 随机)
    │
    ▼  密码与 salt 逐字节 XOR → SHA-256
master_key (32 字节)
    │
    ├─► SHA256(master_key + "ctrl") → control_key → XOR 加密控制区
    │
    └─► SHA256(master_key + entry_id + entry_salt) → blob_key → XOR 加密单条目
```

### 加密模式

| 模式 | Magic Index | Blob 数据 | Group Index | TOC | 场景 |
|------|-------------|-----------|-------------|-----|------|
| FULL | 加密 | 加密 | 加密 | 加密 | 完全保护 |
| METADATA_ONLY | 加密 | 明文 | 加密 | 加密 | 隐藏结构，数据可快速访问 |
| DATA_ONLY | 明文 | 加密 | 明文 | 明文 | 仅保护内容 |

不传 `password` 参数时，文件完全不加密，与无加密行为一致。

### 密码验证

Encryption Params 区存储 `SHA-256(control_key)` 的哈希值。读取时派生密钥后比对哈希，密码错误立即报错，无需尝试解密数据。

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

## 组间关系

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
├── constants.py     # 常量、枚举（EntryType/Compression/EncryptionMode 等）、magic 码表
├── types.py         # FileHeader / TocEntry / MagicEntry / GroupEntry / GroupRelation / EncryptionParams
├── writer.py        # MCPKWriter 写入器（分组 + 时间戳 + 可选加密）
├── reader.py        # MCPKReader 读取器（v1/v2 兼容 + 可选解密）
└── cli.py           # 命令行（pack/list/groups/extract/inspect/verify，均支持 -p 密码）
test_mcpk.py         # 集成测试（20 个用例，支持 --quick / --large-only）
MCPK-v2-Design.md    # v2 编码优化设计（Magic Index + 分组 + Group Index）
MCPK-v2.1-Design.md  # v2.1 增强设计（时间戳 + XOR 加密方案）
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
python test_mcpk.py --test test_10

# 手动指定大小预设
python test_mcpk.py --size xlarge
```

测试覆盖 20 个用例：基本 Roundtrip、分组存储、VIDEO 类型、Magic Index、压缩比、大批量条目、边界情况、v1 兼容、复杂分组场景、性能基准、Blob 排序、Inspect JSON、add_data API、重复打包一致性、压力测试、加密 FULL 模式、加密 METADATA_ONLY 模式、不加密兼容性、时间戳验证、加密+分组组合。

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
- 可选：`zstd`（Zstandard 压缩）、`lz4`（LZ4 压缩）

```bash
pip install zstd lz4
```

## 路线图

### 已完成

- [x] v1 基础格式：Header / TOC / Footer，文档/图片/音频
- [x] v2 编码优化：Magic Index + 分组存储 + Group Index + VIDEO 类型
- [x] v1/v2 向后兼容读取
- [x] 完整时间戳：created_at / modified_at / packed_at
- [x] 可选 XOR 加密：密码派密钥，全量/仅元数据/仅数据三种模式
- [x] Python API + CLI + 20 个集成测试

### 下一步

- [ ] **签名（v2.2）**：Ed25519 数字签名，验证文件来源和完整性
- [ ] **增量追加**：append-only 模式，不重写整个文件即可添加新条目
- [ ] **大文件分块**：单条目 >1GB 时分块存储，支持流式读取
- [ ] **缩略图生成**：图片/视频自动提取缩略图存为关联条目
- [ ] **音频转写**：集成 Whisper 生成文字转录，存为 transcript 关系
- [ ] **全文索引**：为文本类条目构建倒排索引，支持关键词搜索
- [ ] **GUI 查看器**：桌面浏览工具，预览图片、播放音视频
- [ ] **Web 查看器**：本地 HTTP 服务，浏览器中浏览 .mcpk
- [ ] **C++ 实现**：高性能读写库
- [ ] **MeCapsule 集成**：作为应用底层存储格式，时间线视图、知识图谱
