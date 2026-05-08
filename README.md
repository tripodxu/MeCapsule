# MCPK — MeCapsule Package

个人知识管理专用的自定义二进制容器格式。将文档、图片、音频及其元数据打包为单一 `.mcpk` 文件。

## 特性

- 单一文件归档，内建元数据、标签、条目关系
- 每条目独立压缩（自动判断：文本用 zlib，已压缩格式跳过）
- CRC32 完整性校验
- 流式写入，随机读取
- 支持文档、图片、音频三大类，自动识别扩展名和 MIME 类型
- Python API + 命令行工具

## 快速开始

### Python API

```python
from mcpk import MCPKWriter, MCPKReader

# ── 写入 ──
with MCPKWriter("my-capsule.mcpk") as w:
    w.add_file("report.pdf", metadata={"title": "季度报告", "tags": ["工作", "Q1"]})
    w.add_file("photo.jpg")
    w.add_directory("./notes/", prefix="notes/")

# ── 读取 ──
with MCPKReader("my-capsule.mcpk") as r:
    # 列出
    for entry in r.entries:
        print(entry.name, entry.mime_type, entry.original_size)

    # 提取单个
    data = r.extract("report.pdf")

    # 提取全部
    r.extract_all("./output/")

    # 校验
    errors = r.verify()
```

### 命令行

```bash
# 打包
python -m mcpk pack file1.md file2.jpg ./audio/ -o archive.mcpk

# 列出条目
python -m mcpk list archive.mcpk
python -m mcpk list archive.mcpk --type doc
python -m mcpk list archive.mcpk --json

# 提取
python -m mcpk extract archive.mcpk -o ./output/
python -m mcpk extract archive.mcpk -n photo.jpg -o ./output/

# 检查详情
python -m mcpk inspect archive.mcpk

# 验证完整性
python -m mcpk verify archive.mcpk
```

## 文件格式概览

```
┌──────────────────────────────┐
│  File Header (64 B, 固定)     │  magic="MCPK", version, toc_offset, entry_count ...
├──────────────────────────────┤
│  Entry Blob 0                │  原始数据（可能已压缩）
│  Entry Blob 1                │
│  ...                         │
├──────────────────────────────┤
│  TOC (变长)                   │  每条目: type, compression, crc32, name, mime, metadata
├──────────────────────────────┤
│  Footer (16 B, 固定)          │  magic + toc_offset + crc32
└──────────────────────────────┘
```

详细二进制规范见 [MCPK-DataFormat.md](MCPK-DataFormat.md)，设计文档见 [MCPK-Design.md](MCPK-Design.md)。

## 项目结构

```
mcpk/
├── __init__.py      # 包入口，导出主要类
├── __main__.py      # python -m mcpk 支持
├── constants.py     # 常量、枚举、扩展名→MIME映射
├── types.py         # TocEntry / FileHeader 数据类
├── writer.py        # MCPKWriter 写入器
├── reader.py        # MCPKReader 读取器
└── cli.py           # 命令行子命令
test_mcpk.py         # 集成测试
```

## 运行测试

```bash
python test_mcpk.py
```

测试内容：创建临时文件 → 打包 → 读取 → CRC 校验 → 内容比对 → 提取到目录 → 二次校验。

## 依赖

- Python 3.10+
- 标准库：`struct`, `zlib`, `json`, `binascii`
- 可选：`zstd`（Zstandard 压缩）、`lz4`（LZ4 压缩）

```bash
# 可选依赖
pip install zstd lz4
```

## 下一步工作

### 格式增强

- [ ] **v1.1 加密**：AES-256-GCM blob 级加密，密码派生密钥（PBKDF2/Argon2）
- [ ] **v1.2 签名**：Ed25519 数字签名，验证文件来源和完整性
- [ ] **增量追加**：append-only 模式，不重写整个文件即可添加新条目
- [ ] **大文件分块**：单条目 >1GB 时分块存储，支持流式读取

### 功能扩展

- [ ] **缩略图生成**：图片/视频自动提取缩略图存为关联条目
- [ ] **音频转写**：集成 Whisper 生成文字转录，存为 transcript 关系
- [ ] **全文索引**：为文本类条目构建倒排索引，支持关键词搜索
- [ ] **标签系统**：跨条目标签聚合、按标签筛选导出
- [ ] **条目版本**：同一文件的多版本管理，基于 diff 存储

### 生态工具

- [ ] **GUI 查看器**：基于 tkinter/PyQt 的桌面浏览工具，预览图片和播放音频
- [ ] **Web 查看器**：本地 HTTP 服务，浏览器中浏览 .mcpk 内容
- [ ] **C++ 实现**：高性能读写库，适合嵌入式或大规模处理场景
- [ ] **VS Code 插件**：在编辑器中直接浏览和预览 .mcpk 文件

### 与 MeCapsule 集成

- [ ] **导入导出 API**：MeCapsule 应用的核心存储格式
- [ ] **时间线视图**：按 `created_at` 排列条目，生成时间线
- [ ] **关联图谱**：基于 `relationships` 构建条目之间的知识图谱
- [ ] **批量迁移**：从文件夹 / ZIP / 其他格式批量导入到 .mcpk
