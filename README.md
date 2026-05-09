# MCPK — MeCapsule Package

个人知识管理专用的自定义二进制容器格式。将文档、图片、音频、视频及其元数据打包为单一 `.mcpk` 文件。

## 特性

- 单一文件归档，内建元数据、标签、条目关系
- **Magic Index** — 文件头聚合所有文件签名（magic 码），快速识别类型
- **分组存储** — 相关文件（视频+字幕、课程+讲义）物理相邻存放
- **Group Index** — 分组元数据 + 标签 + 组内关系 + 组间关系图
- **完整时间戳** — created_at / modified_at / packed_at
- **双加密模式** — AES-256-GCM 认证加密（高强度）/ XOR 流加密（零依赖），支持全量/仅元数据/仅数据三种模式
- **分组标签** — 每个分组可带多个字符串标签，自动去重
- **组内关系** — 同组条目间的语义关系（字幕、附件、转写、缩略图等 10 种类型）
- **按文件夹打包** — `import_folder()` 自动创建同名分组
- **JSON 索引打包** — `load_index()` 从 JSON 配置批量打包，缺失文件跳过警告
- 不加密模式完全兼容，加密是可选的
- 每条目独立压缩（自动判断：文本用 zlib，已压缩格式跳过）
- CRC32 完整性校验
- 流式写入，随机读取
- 支持文档、图片、音频、视频、代码五大类
- v1/v2 向后兼容
- Python API + 命令行工具

## 安装

```bash
# 核心：零依赖，仅 Python 标准库
# 可选：AES-256-GCM 加密、高级压缩
pip install cryptography zstd lz4
```

---

## 快速开始

```python
from mcpk import MCPKWriter, MCPKReader

# 打包
with MCPKWriter("archive.mcpk") as w:
    w.add_file("report.pdf")
    w.add_file("photo.jpg")

# 读取
with MCPKReader("archive.mcpk") as r:
    data = r.extract("report.pdf")
    r.extract_all("./output/")
```

---

## MCPKWriter API

### 构造函数

```python
MCPKWriter(
    output_path,                    # 输出 .mcpk 文件路径
    *, password=None,               # 加密密码（None=不加密）
    encrypt_mode="full",            # "full" | "metadata_only" | "data_only"
    encryption="aes",               # "aes"（需 cryptography）| "xor"（零依赖）
)
```

用作上下文管理器（`with` 语句），退出时自动调用 `finalize()` 写入文件。

```python
with MCPKWriter("out.mcpk") as w:
    w.add_file("a.txt")
# 退出时自动写入
```

---

### add_file — 添加文件

```python
entry = w.add_file(
    file_path,                      # 文件路径（必需）
    *, arcname=None,                # 包内文件名（默认=原文件名）
    entry_type=None,                # 条目类型（自动推断）
    mime_type=None,                 # MIME 类型（自动推断）
    compression=None,               # 压缩算法（自动推断）
    metadata=None,                  # dict，元数据
    created_at=None,                # int，创建时间戳 Unix ms（默认读源文件）
    modified_at=None,               # int，修改时间戳 Unix ms（默认读源文件）
    group=None,                     # GroupEntry 对象或分组名
    group_name=None,                # 分组名（不存在则自动创建）
) -> TocEntry
```

**示例：**

```python
# 基本用法
w.add_file("report.pdf")

# 自定义包内路径
w.add_file("src/main.py", arcname="code/main.py")

# 指定元数据
w.add_file("photo.jpg", metadata={
    "title": "旅行照片",
    "tags": ["旅行", "2026"],
    "camera": "iPhone 15",
})

# 归入分组
w.add_file("lecture.mp4", group_name="第1讲")
w.add_file("lecture.srt", group_name="第1讲")  # 同名分组自动复用

# 使用 GroupEntry 对象
g = w.create_group("会议", GroupType.MEETING)
w.add_file("recording.mp3", group=g)

# 手动指定类型和压缩
w.add_file("data.bin", entry_type=EntryType.DOCUMENT,
           mime_type="application/octet-stream", compression=Compression.NONE)
```

---

### add_data — 从内存数据添加

```python
entry = w.add_data(
    data,                           # bytes，文件内容（必需）
    name,                           # str，文件名（必需）
    *, entry_type=EntryType.DOCUMENT,
    mime_type="application/octet-stream",
    compression=Compression.ZLIB,
    metadata=None,
    created_at=None,
    modified_at=None,
    group=None,
    group_name=None,
) -> TocEntry
```

**示例：**

```python
# 从字符串
w.add_data("Hello World".encode(), "greeting.txt")

# 从 JSON
import json
config = json.dumps({"version": 2}).encode()
w.add_data(config, "config.json", mime_type="application/json")

# 从内存中的二进制数据
w.add_data(png_bytes, "screenshot.png", entry_type=EntryType.IMAGE,
           mime_type="image/png", compression=Compression.NONE)
```

---

### add_directory — 添加目录

```python
entries = w.add_directory(
    dir_path,                       # 目录路径（必需）
    *, recursive=True,              # 是否递归子目录
    prefix="",                      # 包内路径前缀
    metadata_fn=None,               # Callable[[Path], dict] 条目级元数据回调
    group_name=None,                # 分组名
) -> list[TocEntry]
```

**示例：**

```python
# 添加整个目录
w.add_directory("./notes/")

# 带前缀
w.add_directory("./src/", prefix="code/")

# 非递归
w.add_directory("./docs/", recursive=False)

# 自定义元数据
def meta_fn(path):
    return {"title": path.stem, "size": path.stat().st_size}
w.add_directory("./data/", metadata_fn=meta_fn)

# 归入分组
w.add_directory("./slides/", group_name="课程资料")
```

---

### import_folder — 按文件夹打包

```python
group = w.import_folder(
    folder_path,                    # 文件夹路径（必需）
    *, group_name=None,             # 分组名（默认=文件夹名）
    recursive=True,                 # 是否递归子目录
    tags=None,                      # list[str]，分组标签
    group_type=GroupType.GENERIC,   # 分组类型
    metadata=None,                  # dict，分组元数据
    metadata_fn=None,               # Callable 条目级元数据回调
) -> GroupEntry
```

与 `add_directory` 的区别：`import_folder` 自动以文件夹名创建分组，支持 `tags`。

**示例：**

```python
# 基本用法：文件夹名 "课程资料" 自动成为分组名
w.import_folder("./课程资料/")

# 自定义分组名和标签
w.import_folder("./hw/", group_name="作业", tags=["2026春", "必做"])

# 非递归
w.import_folder("./src/", recursive=False, tags=["源码"])

# 多文件夹打包
w.import_folder("./课程/", tags=["课程"])
w.import_folder("./作业/", tags=["作业"])
w.add_relation("课程", "作业", RelationType.REFERENCES)
```

---

### create_group — 创建分组

```python
group = w.create_group(
    name,                           # 分组名（必需，不可重复）
    group_type=GroupType.GENERIC,   # 分组类型
    *, metadata=None,               # dict，分组元数据
    tags=None,                      # list[str]，分组标签
) -> GroupEntry
```

**示例：**

```python
# 基本
g = w.create_group("第1讲")

# 带类型、标签、元数据
g = w.create_group("机器学习入门", GroupType.COURSE,
    tags=["ML", "深度学习", "入门"],
    metadata={"instructor": "张教授", "week": 1, "credits": 3})

# 添加文件到分组
w.add_file("video.mp4", group=g)
w.add_file("slides.md", group=g)
```

---

### add_tag — 添加标签

```python
w.add_tag(group, tag)
```

- `group`：`GroupEntry` 对象或分组名字符串
- `tag`：标签字符串
- 自动去重（已有标签不重复添加）

**示例：**

```python
g = w.create_group("照片", tags=["旅行"])

# 通过对象添加
w.add_tag(g, "2026")
w.add_tag(g, "风景")

# 通过名称添加
w.add_tag("照片", "夏天")
w.add_tag("照片", "旅行")  # 已存在，不重复

# 最终 tags: ["旅行", "2026", "风景", "夏天"]
```

---

### add_relation — 添加组间关系

```python
rel = w.add_relation(
    source_group,                   # 源分组名（必需）
    target_group,                   # 目标分组名（必需）
    relation_type=RelationType.RELATED,  # 关系类型
    *, description="",              # 描述
) -> GroupRelation
```

**示例：**

```python
w.add_relation("第1讲", "第2讲", RelationType.SEQUEL, description="课程递进")
w.add_relation("第1讲", "学习资料", RelationType.REFERENCES)
w.add_relation("中文版", "英文版", RelationType.VARIANT)
```

---

### add_intra_relation — 添加组内关系

```python
rel = w.add_intra_relation(
    group_name,                     # 分组名（必需）
    *, source,                      # 源文件名（必需）
    target,                         # 目标文件名（必需）
    relation_type=IntraRelationType.CUSTOM,  # 关系类型
    description="",                 # 描述
) -> IntraRelation
```

**示例：**

```python
w.add_file("video.mp4", group_name="课程")
w.add_file("subtitle.srt", group_name="课程")
w.add_file("thumb.jpg", group_name="课程")

w.add_intra_relation("课程", source="subtitle.srt", target="video.mp4",
                      relation_type=IntraRelationType.SUBTITLE_OF)
w.add_intra_relation("课程", source="thumb.jpg", target="video.mp4",
                      relation_type=IntraRelationType.THUMBNAIL_OF)
```

---

### load_index — JSON 索引打包

```python
result = w.load_index(
    index_path,                     # JSON 索引文件路径（必需）
    *, base_dir=".",                # 文件路径的基准目录
) -> dict
```

返回值：`{"loaded": int, "skipped": list, "groups_created": int, "relations_created": int}`

缺失文件跳过并警告，不中止。

**JSON 索引格式：**

```json
{
    "name": "包名称",
    "description": "描述",
    "groups": [
        {
            "name": "组名",
            "type": "COURSE",
            "tags": ["tag1", "tag2"],
            "metadata": {"week": 1},
            "files": [
                {"path": "videos/lecture1.mp4", "title": "第1讲视频"},
                "subs/lecture1.srt"
            ]
        }
    ],
    "standalone_files": [
        {"path": "notes/syllabus.md", "tags": ["大纲"]},
        "config.json"
    ],
    "relations": [
        {"source": "组A", "target": "组B", "type": "SEQUEL", "desc": "顺序"}
    ],
    "intra_relations": [
        {"group": "组名", "source": "a.srt", "target": "a.mp4",
         "type": "SUBTITLE_OF", "desc": "字幕"}
    ]
}
```

**示例：**

```python
with MCPKWriter("course.mcpk") as w:
    result = w.load_index("course_index.json", base_dir="./project/")
    print(f"加载 {result['loaded']} 文件, 跳过 {len(result['skipped'])} 文件")
    for path, reason in result["skipped"]:
        print(f"  跳过: {path} ({reason})")
```

---

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `w.entries` | `list[TocEntry]` | 已添加的所有条目 |
| `w.entry_count` | `int` | 条目数量 |
| `w.groups` | `list[GroupEntry]` | 所有分组 |
| `w.is_encrypted` | `bool` | 是否加密 |

---

## MCPKReader API

### 构造函数

```python
MCPKReader(
    file_path,                      # .mcpk 文件路径（必需）
    *, password=None,               # 解密密码（加密文件必需）
)
```

用作上下文管理器，退出时自动关闭文件。

```python
with MCPKReader("archive.mcpk") as r:
    print(r.version)
    for e in r.entries:
        print(e.name)
```

---

### find — 查找条目

```python
entry = r.find(
    name,                           # 文件名（必需）
    *, group=None,                  # 分组名或 group_id
    index=0,                        # 同名文件索引（0=第一个）
) -> Optional[TocEntry]
```

**示例：**

```python
# 基本查找
entry = r.find("report.pdf")

# 不同组的同名文件
entry_a = r.find("1.txt", group="组A")
entry_b = r.find("1.txt", group="组B")

# 同组同名文件（按添加顺序）
first = r.find("readme.md", index=0)
second = r.find("readme.md", index=1)

# 组合使用
entry = r.find("data.csv", group="实验", index=2)

# 不存在返回 None
assert r.find("不存在.txt") is None
assert r.find("1.txt", group="不存在的组") is None
```

---

### find_all — 查找所有同名条目

```python
entries = r.find_all(
    name,                           # 文件名（必需）
    *, group=None,                  # 分组名或 group_id（可选过滤）
) -> list[TocEntry]
```

**示例：**

```python
# 查找所有同名文件
matches = r.find_all("readme.md")  # 可能有多个

# 按分组过滤
matches = r.find_all("1.txt", group="组A")

# 分组不存在返回空列表
assert r.find_all("1.txt", group="不存在") == []
```

---

### find_group — 查找分组

```python
group = r.find_group(name) -> Optional[GroupEntry]
```

**示例：**

```python
g = r.find_group("第1讲")
if g:
    print(g.name, g.tags, len(g.entry_ids))
```

---

### extract — 提取文件内容

```python
data = r.extract(
    name,                           # 文件名（必需）
    *, group=None,                  # 分组名或 group_id
    index=0,                        # 同名文件索引
) -> bytes
```

所有查找/提取方法均支持 `group` 和 `index` 参数区分同名文件（见 `find` 示例）。

**示例：**

```python
# 基本提取
data = r.extract("report.pdf")
Path("report.pdf").write_bytes(data)

# 区分同名文件（group / index 用法同 find）
data_a = r.extract("1.txt", group="组A")

# 不存在抛出 KeyError
try:
    r.extract("不存在.txt")
except KeyError as e:
    print(e)  # "文件不存在: 不存在.txt"
```

---

### extract_entry — 通过条目对象提取

```python
data = r.extract_entry(entry: TocEntry) -> bytes
```

**示例：**

```python
entry = r.find("report.pdf")
data = r.extract_entry(entry)
```

---

### extract_to — 提取到目录

```python
path = r.extract_to(
    name,                           # 文件名（必需）
    output_dir,                     # 输出目录（必需）
    *, preserve_structure=True,     # 保持子目录结构
    group=None,                     # 分组名或 group_id
    index=0,                        # 同名文件索引
) -> Path
```

**示例：**

```python
# 提取到指定目录
out = r.extract_to("report.pdf", "./output/")
# → ./output/report.pdf

# 不保持结构
out = r.extract_to("src/main.py", "./out/", preserve_structure=False)
# → ./out/main.py（不保留 src/ 前缀）
```

---

### extract_all — 提取全部

```python
paths = r.extract_all(
    output_dir,                     # 输出目录（必需）
    *, preserve_structure=True,     # 保持子目录结构
) -> list[Path]
```

**示例：**

```python
paths = r.extract_all("./output/")
print(f"提取了 {len(paths)} 个文件")
```

---

### extract_group — 按分组提取

```python
paths = r.extract_group(
    group_name,                     # 分组名（必需）
    output_dir,                     # 输出目录（必需）
    *, preserve_structure=True,
) -> list[Path]
```

**示例：**

```python
paths = r.extract_group("第1讲", "./output/")
print(f"提取了第1讲的 {len(paths)} 个文件")
```

---

### list_entries — 按类型列出

```python
entries = r.list_entries(
    entry_type=None,                # EntryType 枚举值（None=全部）
) -> list[TocEntry]
```

**示例：**

```python
from mcpk import EntryType

all_entries = r.list_entries()
videos = r.list_entries(EntryType.VIDEO)
docs = r.list_entries(EntryType.DOCUMENT)
images = r.list_entries(EntryType.IMAGE)
audio = r.list_entries(EntryType.AUDIO)
```

---

### list_group_entries — 列出分组内条目

```python
entries = r.list_group_entries(group_name: str) -> list[TocEntry]
```

**示例：**

```python
entries = r.list_group_entries("第1讲")
for e in entries:
    print(f"  {e.name} ({e.original_size} bytes)")
```

---

### get_metadata — 获取条目元数据

```python
meta = r.get_metadata(
    name,                           # 文件名（必需）
    *, group=None,                  # 分组名或 group_id
    index=0,                        # 同名文件索引
) -> dict
```

**示例：**

```python
meta = r.get_metadata("photo.jpg")
print(meta.get("title"), meta.get("tags"))

# 区分同名文件
meta_a = r.get_metadata("1.txt", group="组A")
```

---

### verify — 完整性校验

```python
errors = r.verify() -> list[str]
```

返回空列表表示通过。逐条目校验 CRC32 和数据大小。

**示例：**

```python
errors = r.verify()
if errors:
    for err in errors:
        print(f"错误: {err}")
else:
    print("校验通过")
```

---

### inspect — 详细信息

```python
info = r.inspect() -> dict
```

返回完整的容器信息，可直接 `json.dumps`。

**示例：**

```python
import json
info = r.inspect()
print(json.dumps(info, ensure_ascii=False, indent=2))
```

返回值顶层结构：

```json
{
    "file": "archive.mcpk",
    "version": 2,
    "encrypted": false,
    "entry_count": 5,
    "total_original_size": 50000,
    "total_stored_size": 30000,
    "overall_ratio": "1.67x",
    "entries": [{ "name": "report.pdf", "type": "DOCUMENT", ... }],
    "groups": [{ "name": "第1讲", "type": "COURSE", "tags": ["ML"], ... }],
    "relations": [{ "source": 0, "target": 1, "type": "SEQUEL" }]
}
```

每个 entry 和 group 内含完整元数据，加密文件还会包含 `encrypt_mode`、`kdf_type`、`kdf_iterations`。

---

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `r.version` | `int` | 格式版本（1 或 2） |
| `r.header` | `FileHeader` | 文件头信息 |
| `r.is_encrypted` | `bool` | 是否加密 |
| `r.encryption_params` | `EncryptionParams \| None` | 加密参数 |
| `r.entries` | `list[TocEntry]` | 所有条目 |
| `r.entry_count` | `int` | 条目数量 |
| `r.magic_entries` | `list[MagicEntry]` | Magic Index 条目 |
| `r.groups` | `list[GroupEntry]` | 所有分组 |
| `r.relations` | `list[GroupRelation]` | 组间关系 |

---

## 数据类型

### TocEntry — 条目

```python
entry.entry_type        # int: EntryType 枚举值
entry.compression       # int: Compression 枚举值
entry.crc32             # int: 原始数据 CRC32
entry.created_at        # int: 源文件创建时间 (Unix ms)
entry.modified_at       # int: 源文件修改时间 (Unix ms)
entry.original_size     # int: 原始文件大小 (bytes)
entry.stored_size       # int: 存储大小 (压缩后, bytes)
entry.blob_offset       # int: blob 在文件中的偏移
entry.name              # str: 文件名
entry.mime_type         # str: MIME 类型
entry.metadata          # str | None: JSON 字符串
entry.group_id          # int: 所属分组 ID (0xFF=无分组)

entry.metadata_dict()   # dict: 解析后的元数据
entry.time_info()       # dict: {"created": ISO, "modified": ISO}
entry.compression_ratio # float: 压缩比 (original/stored)
entry.is_compressed     # bool: 是否压缩
```

### GroupEntry — 分组

```python
group.group_id          # int: 分组 ID
group.name              # str: 分组名
group.group_type        # int: GroupType 枚举值
group.entry_ids         # list[int]: 组内条目 ID 列表
group.tags              # list[str]: 标签列表
group.intra_relations   # list[IntraRelation]: 组内关系
group.metadata          # str | None: JSON 字符串

group.metadata_dict()   # dict: 解析后的元数据
```

### GroupRelation — 组间关系

```python
rel.source_group        # int: 源分组 ID
rel.target_group        # int: 目标分组 ID
rel.relation_type       # int: RelationType 枚举值
rel.description         # str: 描述
```

### IntraRelation — 组内关系

```python
ir.source_entry         # int: 源条目 ID (TOC 索引)
ir.target_entry         # int: 目标条目 ID
ir.relation_type        # int: IntraRelationType 枚举值
ir.description          # str: 描述
```

### FileHeader — 文件头

```python
header.version          # int: 格式版本
header.flags            # int: 标志位
header.packed_at        # int: 打包时间 (Unix ms)
header.entry_count      # int: 条目数量
header.group_count      # int: 分组数量
header.is_encrypted     # bool: 是否加密
header.packed_at_iso()  # str: ISO 8601 格式打包时间
```

### EncryptionParams — 加密参数

```python
ep.kdf_type             # int: KdfType 枚举值
ep.encrypt_mode         # int: EncryptionMode 枚举值
ep.salt                 # bytes: 随机盐
ep.kdf_iterations       # int: PBKDF2 迭代次数
ep.is_aes               # bool: 是否 AES-GCM 模式
```

### MagicEntry — Magic Index 条目

```python
me.entry_id             # int: 条目 ID
me.entry_type           # int: EntryType 枚举值
me.group_id             # int: 所属分组 ID
me.magic_bytes          # bytes: 文件签名 (最多 32 字节)
```

---

## 枚举

### EntryType — 条目类型

| 值 | 名称 | 说明 |
|----|------|------|
| 0x01 | DOCUMENT | 文档 |
| 0x02 | IMAGE | 图片 |
| 0x03 | AUDIO | 音频 |
| 0x04 | VIDEO | 视频 |

### Compression — 压缩算法

| 值 | 名称 | 说明 |
|----|------|------|
| 0x00 | NONE | 不压缩 |
| 0x01 | ZLIB | 通用压缩 |
| 0x02 | ZSTD | 高压缩比 + 快速解压 |
| 0x03 | LZ4 | 极速压缩/解压 |

### GroupType — 分组类型

| 值 | 名称 | 说明 | 典型场景 |
|----|------|------|----------|
| 0x00 | GENERIC | 通用 | 任意文件集合 |
| 0x01 | VIDEO_SUBTITLE | 视频+字幕 | 电影/课程配字幕 |
| 0x02 | DOCUMENT_SET | 文档集合 | 报告+数据+图表 |
| 0x03 | MEDIA_ALBUM | 媒体专辑 | 相册、音乐集 |
| 0x04 | COURSE | 课程 | 视频+讲义+作业 |
| 0x05 | MEETING | 会议 | 录音+纪要+附件 |

### RelationType — 组间关系

| 值 | 名称 | 说明 | 示例 |
|----|------|------|------|
| 0x00 | SEQUEL | 顺序/续集 | 第1讲 → 第2讲 |
| 0x01 | RELATED | 语义关联 | 两个相关会议 |
| 0x02 | DEPENDS_ON | 依赖 | 实验报告 → 原始数据 |
| 0x03 | VARIANT | 变体/版本 | 中文版 → 英文版 |
| 0x04 | REFERENCES | 引用 | 论文 → 参考文献集 |

### IntraRelationType — 组内关系

| 值 | 名称 | 说明 | 示例 |
|----|------|------|------|
| 0x00 | SUBTITLE_OF | 字幕属于视频 | lecture.srt → lecture.mp4 |
| 0x01 | ATTACHMENT_OF | 附件属于主体 | appendix.pdf → report.docx |
| 0x02 | TRANSCRIPT_OF | 转写属于音视频 | transcript.txt → meeting.mp3 |
| 0x03 | THUMBNAIL_OF | 缩略图属于原图 | thumb.jpg → photo.jpg |
| 0x04 | ANNOTATION_OF | 批注属于文档 | notes.md → slides.pdf |
| 0x05 | CHAPTER_OF | 章节属于整体 | ch1.md → book.pdf |
| 0x06 | SUPPLEMENT_OF | 补充材料 | extra.pdf → main.pdf |
| 0x07 | DERIVED_FROM | 派生自 | extract.txt → scan.pdf |
| 0x08 | VERSION_OF | 另一版本 | v2.docx → v1.docx |
| 0xFF | CUSTOM | 自定义 | 用户自定义 |

### EncryptionMode — 加密模式

| 值 | 名称 | MI | Blobs | GI | TOC | 场景 |
|----|------|-----|-------|-----|-----|------|
| 0x00 | NONE | - | - | - | - | 不加密 |
| 0x01 | FULL | 加密 | 加密 | 加密 | 加密 | 完全保护 |
| 0x02 | METADATA_ONLY | 加密 | 明文 | 加密 | 加密 | 隐藏结构，数据可快速访问 |
| 0x03 | DATA_ONLY | 明文 | 加密 | 明文 | 明文 | 仅保护内容 |

### KdfType — 密钥派生

| 值 | 名称 | 说明 | 依赖 |
|----|------|------|------|
| 0x01 | SHA256_XOR | SHA-256 + XOR 流加密 | 零依赖 |
| 0x02 | PBKDF2_AES | PBKDF2 + AES-256-GCM | cryptography |

---

## 支持的文件类型

| 类型 | 扩展名 | EntryType | 压缩 |
|------|--------|-----------|------|
| 文档 | .md .txt .json .csv .html .xml .yaml .toml .ini .cfg .log | DOCUMENT | zlib |
| 文档 | .pdf .docx .doc .xlsx .pptx | DOCUMENT | 无 |
| 字幕 | .srt .vtt .ass | DOCUMENT | zlib |
| 代码 | .py .js .ts .jsx .tsx .java .c .cpp .h .hpp .go .rs .rb .php .swift .kt .sh .bash .bat .ps1 .css .scss .sql .r .lua | DOCUMENT | zlib |
| 图片 | .jpg .png .gif .webp .svg .ico | IMAGE | 无 |
| 图片 | .bmp .tiff | IMAGE | zlib |
| 音频 | .mp3 .ogg .flac .aac .m4a .wma | AUDIO | 无 |
| 音频 | .wav | AUDIO | zstd |
| 视频 | .mp4 .mkv .avi .mov .webm .flv .wmv .ts .m4v | VIDEO | 无 |

未识别的扩展名默认为 `DOCUMENT` + `application/octet-stream` + `zlib`。

---

## 命令行参考

### pack — 打包

```bash
# 基本打包
python -m mcpk pack file1.md file2.jpg -o archive.mcpk

# 打包目录
python -m mcpk pack ./notes/ -o archive.mcpk

# 指定分组
python -m mcpk pack video.mp4 sub.srt -o course.mcpk --group "第1讲"

# 每个目录自动成组
python -m mcpk pack folderA/ folderB/ -o output.mcpk --auto-group

# JSON 索引打包
python -m mcpk pack --index index.json --base-dir ./project/ -o output.mcpk

# AES-GCM 加密（默认）
python -m mcpk pack private.md -o secret.mcpk -p "password"

# XOR 加密（零依赖）
python -m mcpk pack video.mp4 -o compat.mcpk -p "password" --encryption xor

# 仅加密元数据
python -m mcpk pack video.mp4 -o meta.mcpk -p "password" --encrypt-mode metadata_only

# 路径前缀
python -m mcpk pack ./src/ -o archive.mcpk --prefix "code/"
```

| 参数 | 说明 |
|------|------|
| `sources` | 源文件或目录（多个） |
| `-o, --output` | 输出路径 |
| `--prefix` | 包内路径前缀 |
| `--group` | 将所有文件归入指定分组 |
| `--auto-group` | 每个目录自动成组 |
| `--index` | JSON 索引文件路径 |
| `--base-dir` | 索引基准目录（默认 `.`） |
| `-p, --password` | 加密密码 |
| `--encrypt-mode` | `full` / `metadata_only` / `data_only` |
| `--encryption` | `aes`（默认）/ `xor` |

### list — 列出条目

```bash
python -m mcpk list archive.mcpk
python -m mcpk list archive.mcpk --type video
python -m mcpk list archive.mcpk --json
python -m mcpk list secret.mcpk -p "password"
```

### groups — 列出分组

```bash
python -m mcpk groups archive.mcpk
python -m mcpk groups secret.mcpk -p "password"
```

显示分组名、类型、标签、条目列表、组内关系、组间关系。

### extract — 提取

```bash
# 提取全部
python -m mcpk extract archive.mcpk -o ./output/

# 提取单个文件
python -m mcpk extract archive.mcpk -n photo.jpg -o ./output/

# 按分组提取
python -m mcpk extract archive.mcpk -g "第1讲" -o ./output/

# 加密文件
python -m mcpk extract secret.mcpk -o ./output/ -p "password"
```

### inspect — 检查详情

```bash
python -m mcpk inspect archive.mcpk
python -m mcpk inspect archive.mcpk --json
python -m mcpk inspect secret.mcpk -p "password"
```

### verify — 验证完整性

```bash
python -m mcpk verify archive.mcpk
python -m mcpk verify secret.mcpk -p "password"
```

---

## 加密架构

### AES-256-GCM（默认）

```
密码 → PBKDF2-HMAC-SHA256 (600000次, salt=32B) → master_key
master_key → HKDF(info="mcpk-ctrl") → control_key → 加密 MI/GI/TOC
master_key → HKDF(info="mcpk-data") → data_key_base
data_key_base → HKDF(salt=entry_salt, info="mcpk-blob"+entry_id) → blob_key → 加密 blob
```

- 每个区域（MI/GI/TOC）和每个 blob 使用独立 nonce
- AES-GCM 认证标签（16B）覆盖 nonce + ciphertext + AAD
- 篡改任何字节都会导致解密失败

### XOR 流加密（零依赖）

```
password + salt → XOR混合 → SHA-256 → master_key
master_key → SHA-256(+ "ctrl") → control_key → XOR 加密 MI/GI/TOC
master_key → SHA-256(+ entry_id + entry_salt) → blob_key → XOR 加密 blob
```

---

## 文件格式概览

```
┌────────────────────────────────┐
│  File Header (64B)             │  magic, version, flags, offsets
│  group_index_size 字段         │  存储 mi_encrypted_size
├────────────────────────────────┤
│  Encryption Params (56/76B)    │  [加密] salt + key_verify
├────────────────────────────────┤
│  Magic Index (变长)            │  文件签名 + 类型 + 分组
├────────────────────────────────┤
│  Data Section (变长)           │  按分组排列的 blobs
├────────────────────────────────┤
│  Group Index (变长)            │  分组 + 标签 + 组内关系 + 组间关系
├────────────────────────────────┤
│  TOC (变长)                    │  条目索引 + 元数据
├────────────────────────────────┤
│  Footer (16B)                  │  magic + toc_offset + crc32
└────────────────────────────────┘
```

详细二进制规范见 [MCPK-DataFormat.md](MCPK-DataFormat.md)，v2 设计见 [MCPK-v2-Design.md](MCPK-v2-Design.md)，v2.2 设计见 [MCPK-v2.2-Design.md](MCPK-v2.2-Design.md)，开发路线图见 [ROADMAP.md](ROADMAP.md)。

---

## 项目结构

```
mcpk/
├── __init__.py      # 包入口，导出所有公开类和枚举
├── __main__.py      # python -m mcpk
├── constants.py     # 常量、枚举、扩展名映射、magic 码表
├── types.py         # FileHeader / TocEntry / GroupEntry / IntraRelation / ...
├── writer.py        # MCPKWriter（写入 + 加密 + import_folder + load_index）
├── reader.py        # MCPKReader（读取 + 解密 + 查找/提取）
└── cli.py           # 命令行工具
test_mcpk.py         # 49 个集成测试
MCPK-DataFormat.md   # 二进制格式规范
MCPK-v2-Design.md    # v2 设计方案
MCPK-v2.2-Design.md  # v2.2 增强设计
ROADMAP.md           # 开发路线图
requirements.txt     # 可选依赖
```

## 运行测试

```bash
python test_mcpk.py --quick         # 快速（tiny 文件，~4s）
python test_mcpk.py                 # 完整（含 medium 文件）
python test_mcpk.py --large-only    # 大文件（50~100MB）
python test_mcpk.py --test test_21  # 单个测试
python test_mcpk.py --size small    # 指定大小预设
```

测试覆盖：基本 Roundtrip、分组存储、Magic Index、压缩比、大批量条目、v1 兼容、XOR/AES-GCM 加密三种模式、分组标签、组内关系、import_folder、JSON 索引打包、时间戳验证等 49 个用例。

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
- 可选：`cryptography`（AES-256-GCM）、`zstd`、`lz4`

`cryptography` 不安装时仍可使用 XOR 加密和所有非加密功能。
