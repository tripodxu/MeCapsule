# MCPK v2.2 设计文档

**MeCapsule Package (MCPK) v2.2 — AES-GCM 加密 + 标签 + 组内关系 + 索引打包**

> 在 v2.1 基础上新增：AES-256-GCM 认证加密、分组标签系统、组内条目关系、按文件夹打包、JSON 索引打包。

---

## 1. 新增特性概览

| 特性 | 说明 | 依赖 |
|------|------|------|
| AES-256-GCM 加密 | PBKDF2 密钥派生 + AES-GCM 认证加密，防篡改 | `cryptography` |
| XOR 加密保留 | v2.1 的 SHA256_XOR 模式完全保留 | 零依赖 |
| 分组标签 (Tags) | 每个分组可带多个字符串标签，自动去重 | 无 |
| 组内关系 (IntraRelation) | 同组内条目间的语义关系（10 种类型） | 无 |
| 按文件夹打包 | `import_folder()` 自动创建同名分组 | 无 |
| JSON 索引打包 | `load_index()` 从 JSON 配置批量打包 | 无 |

---

## 2. 加密方案

### 2.1 双模式架构

```
KdfType 枚举:
  SHA256_XOR = 0x01  # v2.1 兼容，零依赖
  PBKDF2_AES = 0x02  # v2.2 高强度，需 cryptography
```

用户通过 `encryption="aes"` 或 `encryption="xor"` 选择。默认 `"aes"`，若 `cryptography` 不可用则抛出 `ImportError` 提示安装或切换 XOR。

### 2.2 AES-256-GCM 密钥派生

```
用户密码 (UTF-8)
    │
    ▼
PBKDF2-HMAC-SHA256 (iterations=600000, salt=32B)
    │
    ▼
master_key (32B)
    │
    ├──► HKDF-SHA256(info="mcpk-ctrl") → control_key (32B)
    │    用于加密控制区
    │
    └──► HKDF-SHA256(info="mcpk-data") → data_key_base (32B)
         派生每条目的 blob 密钥
```

每条目 blob 密钥：
```
blob_key_i = HKDF-SHA256(salt=entry_salt_16B, info="mcpk-blob" + entry_id_4B)
             .derive(data_key_base)
```

### 2.3 AES-GCM 加密/解密

**加密：**
```
nonce = random(12B)
ciphertext + tag(16B) = AES-256-GCM(key, nonce, plaintext, aad)
输出 = nonce(12B) + ciphertext + tag(16B)
```

**解密：**
```
nonce = data[:12]
ct = data[12:]
plaintext = AES-256-GCM-Decrypt(key, nonce, ct, aad)
# tag 校验失败 → 捕获 InvalidTag，包装为 MCPKError
```

**AAD (附加认证数据)：**
- 控制区：`b"mcpk-ctrl"`
- 数据区：`struct.pack("<I", entry_id)`

### 2.4 Encryption Params 区（76 字节，新版）

```
偏移   大小    类型        字段              说明
──────────────────────────────────────────────────────────────
0x00   4B     char[4]     params_magic      "ENC0"
0x04   1B     uint8_t     kdf_type          0x02=PBKDF2_AES
0x05   1B     uint8_t     encrypt_mode      FULL/METADATA/DATA
0x06   2B     bytes       reserved
0x08   4B     uint32_le   kdf_iterations    PBKDF2 迭代次数 (600000)
0x0C   32B    bytes       salt              256-bit 随机盐
0x2C   32B    bytes       key_verify        SHA-256(master_key || "verify")
──────────────────────────────────────────────────────────────
        76B 总计
```

旧版 (kdf_type=0x01) 仍为 56 字节，读取时根据 kdf_type 自动选择解析格式。

### 2.5 加密数据区布局

```
每条目 blob 在磁盘上的存储：
  [entry_salt: 16B] [AES-GCM(nonce:12B + ciphertext + tag:16B)]
  或
  [entry_salt: 16B] [XOR(encrypted_data)]
```

stored_size 记录的是包含 entry_salt 在内的总字节数。

### 2.6 Header 中的 mi_encrypted_size

v2.2 引入了一个关键的布局改进：**Header 的 `group_index_size` 字段存储 MI 加密后的精确大小**（而非 GI 大小）。GI 大小通过偏移差计算：`gi_encrypted_size = toc_offset - gi_offset`。

这解决了 AES-GCM 认证加密下的 MI 定位问题：MI 和 data blobs 在文件中物理相邻，无法仅从偏移量区分。直接存储 MI 大小后，reader 可以精确读取 MI 字节，避免把 blobs 数据混入 MI 解密导致 `InvalidTag`。

### 2.7 错误处理

AES-GCM 解密失败（`InvalidTag`）统一捕获并包装为 `MCPKError`：
- 控制区解密失败：`"数据已损坏或密码错误（AES-GCM 认证失败）"`
- Blob 解密失败：`"数据已损坏: {name}（AES-GCM 认证失败）"`

### 2.8 向后兼容

| 场景 | 处理 |
|------|------|
| v2.2 reader 读 v2.1 XOR 文件 | kdf_type=0x01 → 走旧路径 |
| v2.2 reader 读 v2.2 AES 文件 | kdf_type=0x02 → 走新路径 |
| v2.1 reader 读 v2.2 文件 | header 布局兼容（mi_encrypted_size 字段被旧 reader 忽略） |
| 无 cryptography 写入 AES | 抛出 ImportError 提示安装或切换 XOR |
| 无 cryptography 读取 AES | 抛出 MCPKError 提示安装 |

---

## 3. 分组标签系统

### 3.1 数据结构

```python
@dataclass
class GroupEntry:
    # ... 原有字段 ...
    tags: list[str] = field(default_factory=list)
```

### 3.2 Group Index 二进制格式扩展

```
Group Entry 新格式（在原有 metadata 之后）:
  +...    2B    tag_count
  +...    变长  tags[]           每个 tag: 2B len + UTF-8
  +...    2B    entry_id_count   (原有)
  +...    变长  entry_ids[]      (原有)
  +...    2B    intra_rel_count  (新增)
  +...    变长  intra_relations[] (新增)
```

### 3.3 API

```python
# 创建时指定
g = writer.create_group("组名", tags=["tag1", "tag2"])

# 动态添加（自动去重）
writer.add_tag(g, "tag3")
writer.add_tag("组名", "tag4")
```

---

## 4. 组内关系 (IntraRelation)

### 4.1 枚举定义

```python
class IntraRelationType(IntEnum):
    SUBTITLE_OF    = 0x00  # 字幕属于视频
    ATTACHMENT_OF  = 0x01  # 附件属于主体
    TRANSCRIPT_OF  = 0x02  # 转写属于音视频
    THUMBNAIL_OF   = 0x03  # 缩略图属于原图
    ANNOTATION_OF  = 0x04  # 批注属于文档
    CHAPTER_OF     = 0x05  # 章节属于整体
    SUPPLEMENT_OF  = 0x06  # 补充材料
    DERIVED_FROM   = 0x07  # 派生自
    VERSION_OF     = 0x08  # 另一版本
    CUSTOM         = 0xFF  # 自定义
```

与组间 `RelationType` 分离，语义不同。

### 4.2 二进制格式

```
IntraRelation (每条):
  source_entry:  4B (uint32)
  target_entry:  4B (uint32)
  relation_type: 2B (uint16)
  desc_len:      2B (uint16)
  description:   变长 UTF-8
```

source/target 是条目在 TOC 中的全局索引（entry_id）。

### 4.3 API

```python
writer.add_intra_relation(
    "组名",
    source="video.mp4",
    target="subtitle.srt",
    relation_type=IntraRelationType.SUBTITLE_OF,
    description="中文字幕",
)
```

---

## 5. 按文件夹打包

### 5.1 API

```python
group = writer.import_folder(
    "path/to/folder",
    group_name=None,       # 默认=文件夹名
    recursive=True,        # 递归子目录
    tags=["tag1"],         # 分组标签
    group_type=GroupType.GENERIC,
    metadata={...},        # 分组元数据
    metadata_fn=None,      # 条目级元数据回调
)
```

### 5.2 CLI

```bash
python -m mcpk pack folder_A/ folder_B/ -o output.mcpk --auto-group
```

`--auto-group`：每个顶级目录自动创建同名分组。

---

## 6. JSON 索引打包

### 6.1 JSON Schema

```json
{
    "name": "包名称",
    "description": "描述",
    "groups": [
        {
            "name": "组名",
            "type": "COURSE",
            "tags": ["tag1"],
            "metadata": {},
            "files": [
                {"path": "relative/path.ext", "title": "显示名"},
                "another/file.ext"
            ]
        }
    ],
    "standalone_files": [
        {"path": "file.ext", "tags": ["optional"]},
        "simple_file.txt"
    ],
    "relations": [
        {"source": "组A", "target": "组B", "type": "SEQUEL", "desc": "..."}
    ],
    "intra_relations": [
        {"group": "组名", "source": "a.mp4", "target": "a.srt",
         "type": "SUBTITLE_OF", "desc": "..."}
    ]
}
```

### 6.2 缺失文件处理

缺失文件**跳过并警告**，不中止打包。返回值包含 `skipped` 列表。

### 6.3 API

```python
result = writer.load_index("index.json", base_dir="./project/")
# result = {"loaded": 5, "skipped": [("path", "reason")], "groups_created": 2, "relations_created": 1}
```

### 6.4 CLI

```bash
python -m mcpk pack --index index.json --base-dir ./project/ -o output.mcpk [--password ...]
```

---

## 7. CLI 参数变更

| 命令 | 新增参数 | 说明 |
|------|----------|------|
| pack | `--encryption aes\|xor` | 加密算法选择（默认 aes） |
| pack | `--auto-group` | 每个目录自动成组 |
| pack | `--index` | JSON 索引文件路径 |
| pack | `--base-dir` | 索引文件基准目录 |
| groups | (无) | 展示增加 tags 和组内关系 |

---

## 8. 文件变更清单

| 文件 | 变更说明 |
|------|----------|
| `mcpk/__init__.py` | 版本 2.2.0，导出 `IntraRelationType`、`IntraRelation` |
| `mcpk/constants.py` | 新增 `KdfType.PBKDF2_AES`、`IntraRelationType`、AES-GCM 常量、EP 格式 |
| `mcpk/types.py` | `EncryptionParams` 扩展、`GroupEntry` 增加 tags/intra_relations、新增 `IntraRelation` |
| `mcpk/writer.py` | AES-GCM 加密、`import_folder()`、`load_index()`、`add_tag()`、`add_intra_relation()` |
| `mcpk/reader.py` | AES-GCM 解密、tags/intra_relations 解析、`InvalidTag` 包装 |
| `mcpk/cli.py` | `--encryption`、`--auto-group`、`--index`/`--base-dir` 参数 |
| `test_mcpk.py` | 新增 test_21 ~ test_48（28 个测试） |

---

## 9. 测试覆盖

| 测试 ID | 名称 | 覆盖范围 |
|---------|------|----------|
| test_21 | AES-GCM FULL roundtrip | 加密/解密/密码验证 |
| test_22 | AES-GCM METADATA_ONLY | 部分加密模式 |
| test_23 | AES-GCM DATA_ONLY | 部分加密模式 |
| test_24 | AES-GCM 错误密码 | 安全验证 |
| test_25 | AES-GCM 篡改检测 | GCM 认证 |
| test_26 | XOR 向后兼容 | 向后兼容 |
| test_27 | AES-GCM + 分组 + 关系 | 组合场景 |
| test_28 | 分组 Tag 基本 | Tags |
| test_29 | 动态添加 Tag | Tags API |
| test_30 | 组内关系基本 | IntraRelation |
| test_31 | 多组内关系 | IntraRelation |
| test_32 | Tags + 组内关系 + 加密 | 全特性组合 |
| test_33 | import_folder 基本 | 文件夹打包 |
| test_34 | import_folder 多文件夹 | 文件夹打包 |
| test_35 | import_folder 非递归 | 文件夹打包 |
| test_36 | JSON 索引基本 | 索引打包 |
| test_37 | JSON 索引缺失文件 | 错误处理 |
| test_38 | JSON 索引 + 组内关系 | 索引打包 |
| test_39 | JSON 索引 + 加密 | 组合场景 |
| test_40 | inspect 新字段 | 输出格式 |
| test_41 | Tag 去重 | 边界情况 |
| test_42 | 大量 Tag | 压力测试 |
| test_43 | CUSTOM 组内关系 | 枚举覆盖 |
| test_44 | import_folder + tags + 关系 | 全特性 |
| test_45 | 所有 IntraRelationType | 枚举覆盖 |
| test_46 | 不加密兼容 | 回归测试 |
| test_47 | JSON 索引空/最小 | 边界情况 |
| test_48 | JSON 索引复杂场景 | 全特性综合 |

共计 **48 个测试用例**（原有 20 + 新增 28）。
