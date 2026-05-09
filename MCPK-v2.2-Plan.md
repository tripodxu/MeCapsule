# MCPK v2.2 实施计划

**状态：已完成** — 所有 Phase 均已实现，49 个测试全部通过。本文档保留作为实施记录。

**基于用户确认：AES-GCM 用 cryptography 库 / 缺失文件跳过警告 / 组内关系新增专用类型**

---

## 确认决策

| 项 | 决策 |
|----|------|
| AES-256-GCM 依赖 | `cryptography` 库，XOR 模式保持零依赖 |
| JSON 索引缺失文件 | 跳过 + 打印警告，不中止 |
| 组内关系类型 | 新增 `IntraRelationType` 枚举，与 `RelationType` 分离 |

---

## Phase 1：AES-256-GCM 高强度加密

### 1.1 constants.py 变更

```python
# KdfType 枚举扩展
class KdfType(IntEnum):
    SHA256_XOR  = 0x01  # 保留
    PBKDF2_AES  = 0x02  # 新增：PBKDF2 + AES-256-GCM

# EncryptionMode 不变（NONE/FULL/METADATA_ONLY/DATA_ONLY 四种）

# Encryption Params 区从 56B 扩展为 76B：
#   params_magic(4s) + kdf_type(B) + encrypt_mode(B) + reserved(2s)
#   + kdf_iterations(I)        ← 新增 4B
#   + salt(32s)                ← 从 16B 扩展到 32B
#   + key_verify(32s)          ← 从 control_key_hash 改名，仍 32B
ENCRYPTION_PARAMS_FMT_V2 = "<4sBB 2s I 32s 32s"
ENCRYPTION_PARAMS_SIZE_V2 = 76  # struct.calcsize = 4+1+1+2+4+32+32 = 76
```

Header 布局不变，`ep_size` 字段自动记录 56 或 80。

### 1.2 writer.py 变更

新增依赖导入：
```python
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
```

密钥派生（PBKDF2 模式）：
```python
def _derive_key_pbkdf2(password: str, salt: bytes, iterations: int = 600_000) -> bytes:
    """PBKDF2-SHA256 派生 256-bit 主密钥。"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(password.encode("utf-8"))

def _derive_subkeys(master_key: bytes):
    """从主密钥派生控制区密钥和数据区密钥基（HKDF）。"""
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes
    control_key = HKDF(algorithm=hashes.SHA256(), length=32,
                       salt=None, info=b"mcpk-ctrl").derive(master_key)
    data_key_base = HKDF(algorithm=hashes.SHA256(), length=32,
                         salt=None, info=b"mcpk-data").derive(master_key)
    return control_key, data_key_base
```

AES-GCM 加密/解密：
```python
def aes_gcm_encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """AES-256-GCM 加密，返回 nonce(12B) + ciphertext + tag(16B)。"""
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(nonce, plaintext, aad)
    return nonce + ct  # nonce(12) + ciphertext + tag(16)

def aes_gcm_decrypt(key: bytes, data: bytes, aad: bytes = b"") -> bytes:
    """AES-256-GCM 解密，输入 nonce(12B) + ciphertext + tag(16B)。"""
    nonce = data[:12]
    ct = data[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ct, aad)
```

MCPKWriter.__init__ 变更：
```python
# 新增 encryption 参数（"aes" | "xor"）
# 默认 "aes"，若 cryptography 不可用则回退 "xor" 并警告
```

finalize() 中加密逻辑：
- `kdf_type == PBKDF2_AES` 时：
  - 控制区：`aes_gcm_encrypt(control_key, raw_data, aad=b"mcpk-ctrl")`
  - 数据区每条目：`aes_gcm_encrypt(blob_key_i, blob_data, aad=struct.pack("<I", entry_id))`
  - stored_size 更新为 nonce(12) + ciphertext + tag(16) 的总长

### 1.3 reader.py 变更

_load_v2() 中解密逻辑：
- 读取 `kdf_type`，分派到 XOR 解密 或 AES-GCM 解密
- AES-GCM 解密失败（tag 校验）→ 抛出 `MCPKError("数据已损坏或密码错误")`
- 提取 blob 时：先读 nonce(12B) + 密文 + tag(16B)，调用 `aes_gcm_decrypt`

### 1.4 向后兼容

| 场景 | 处理 |
|------|------|
| v2.2 reader 读 v2.1 XOR 文件 | 正常（kdf_type=0x01 走旧路径） |
| v2.2 reader 读 v2.2 AES 文件 | 正常（kdf_type=0x02 走新路径） |
| v2.1 reader 读 v2.2 AES 文件 | 检测到 ENCRYPTED flag，报错提示升级 |
| 无 cryptography 时读 AES 文件 | 报错：`请安装 cryptography: pip install cryptography` |
| 无 cryptography 时写入 | 自动回退 XOR，打印警告 |

---

## Phase 2：分组 Tag + 组内关系

### 2.1 constants.py 新增枚举

```python
class IntraRelationType(IntEnum):
    """组内关系类型（与 RelationType 分离）。"""
    SUBTITLE_OF    = 0x00  # 字幕属于视频
    ATTACHMENT_OF  = 0x01  # 附件属于主体文件
    TRANSCRIPT_OF  = 0x02  # 转写文本属于音频/视频
    THUMBNAIL_OF   = 0x03  # 缩略图属于原图
    ANNOTATION_OF  = 0x04  # 批注属于文档
    CHAPTER_OF     = 0x05  # 章节属于整体
    SUPPLEMENT_OF  = 0x06  # 补充材料属于主体
    DERIVED_FROM   = 0x07  # 由某文件派生（如 PDF→文本提取）
    VERSION_OF     = 0x08  # 某文件的另一版本
    CUSTOM         = 0xFF  # 用户自定义
```

### 2.2 types.py 变更

GroupEntry 扩展：
```python
@dataclass
class GroupEntry:
    group_id: int = 0
    entry_ids: list[int] = field(default_factory=list)
    group_type: int = 0x00
    name: str = ""
    metadata: Optional[str] = None
    tags: list[str] = field(default_factory=list)           # 新增
    intra_relations: list[IntraRelation] = field(default_factory=list)  # 新增
```

新增 IntraRelation 数据类：
```python
@dataclass
class IntraRelation:
    """组内条目间关系。"""
    source_entry: int = 0   # 条目 ID（在 TOC 中的索引）
    target_entry: int = 0
    relation_type: int = 0x00  # IntraRelationType
    description: str = ""
```

### 2.3 writer.py 变更

create_group() 增加 `tags` 参数：
```python
def create_group(self, name, group_type=GroupType.GENERIC, *,
                 tags=None, metadata=None) -> GroupEntry:
    # tags: list[str]，存入 GroupEntry.tags
```

新增 `add_tag()` 方法：
```python
def add_tag(self, group_name: str, tag: str):
    """为已有分组添加标签。"""
```

新增 `add_intra_relation()` 方法：
```python
def add_intra_relation(self, group_name: str, *,
                       source: str, target: str,
                       relation_type: int = IntraRelationType.CUSTOM,
                       description: str = ""):
    """添加组内条目间关系。source/target 为文件名。"""
```

### 2.4 Group Index 二进制格式扩展

Group Entry 新格式：
```
+0x00    1B    group_id
+0x01    1B    entry_count
+0x02    2B    group_type
+0x04    2B    name_len
+0x06    变长  group_name
+...     2B    meta_len
+...     变长  group_metadata JSON
+...     2B    tag_count               ← 新增
+...     变长  tags[]                   ← 新增：每个 tag = 2B len + UTF-8
+...     2B    entry_id_count           ← 原有
+...     变长  entry_ids[]              ← 原有：每个 4B
+...     2B    intra_rel_count          ← 新增
+...     变长  intra_relations[]        ← 新增：每条 = 4B src + 4B tgt + 2B type + 2B desc_len + desc
```

### 2.5 CLI 展示增强

`cmd_groups` 输出中增加 tags 和组内关系：
```
  [COURSE] 第1讲 (ID=0, 3 条目)
    标签: ML, 深度学习, 入门
    条目:
      [VID] lecture1.mp4 (50MB)
      [DOC] lecture1.srt (12KB)  --[SUBTITLE_OF]--> lecture1.mp4
      [DOC] slides1.md (5KB)
    元数据: {"week": 1}
```

---

## Phase 3：按文件夹打包（import_folder）

### 3.1 writer.py 新增方法

```python
def import_folder(self, folder_path: Union[str, Path], *,
                  group_name: Optional[str] = None,
                  recursive: bool = True,
                  tags: Optional[list[str]] = None,
                  group_type: int = GroupType.GENERIC,
                  metadata: Optional[dict] = None,
                  metadata_fn=None,
                  ) -> GroupEntry:
    """
    导入文件夹，自动创建同名分组。

    Args:
        folder_path: 文件夹路径
        group_name: 分组名（默认=文件夹名）
        recursive: 是否递归子目录
        tags: 分组标签
        group_type: 分组类型
        metadata: 分组元数据
        metadata_fn: 条目级元数据回调

    Returns:
        创建的 GroupEntry
    """
```

### 3.2 CLI 变更

`cmd_pack` 增加 `--auto-group` 参数：
```bash
python -m mcpk pack folder_A/ folder_B/ -o output.mcpk --auto-group
```

当 `--auto-group` 启用时，每个顶级目录自动调用 `import_folder()`，顶级文件归入默认分组或不分组。

---

## Phase 4：JSON 索引打包

### 4.1 writer.py 新增方法

```python
def load_index(self, index_path: Union[str, Path], *,
               base_dir: Union[str, Path] = ".") -> dict:
    """
    从 JSON 索引文件加载打包配置。

    JSON 格式:
    {
        "name": "包名称",
        "description": "描述",
        "groups": [
            {
                "name": "组名",
                "type": "COURSE",         # 可选，GroupType 名称
                "tags": ["tag1", "tag2"], # 可选
                "metadata": {},            # 可选
                "files": [
                    {"path": "relative/path.ext", "title": "显示名"},
                    "another/file.ext"             # 简写形式
                ]
            }
        ],
        "standalone_files": [
            {"path": "file.ext", "tags": ["optional"]}
        ],
        "relations": [
            {"source": "组A", "target": "组B", "type": "SEQUEL", "desc": "..."}
        ],
        "intra_relations": [
            {"group": "组名", "source": "a.mp4", "target": "a.srt",
             "type": "SUBTITLE_OF", "desc": "..."}
        ]
    }

    Returns:
        {
            "loaded": int,       # 成功加载的文件数
            "skipped": list,     # 跳过的文件 [(path, reason)]
            "groups_created": int,
            "relations_created": int,
        }
    """
```

### 4.2 CLI 变更

```bash
python -m mcpk pack --index index.json --base-dir ./project/ -o output.mcpk [--password ...]
```

输出示例：
```
加载索引: index.json
  [OK] 第1讲: + lecture1.mp4 (50MB)
  [OK] 第1讲: + lecture1.srt (12KB)
  [WARN] 第1讲: 跳过 slides1.md (文件不存在)
  [OK] 第2讲: + lecture2.mp4 (48MB)
  ...
打包完成: output.mcpk
  组数: 2, 关系: 1
  加载: 5 文件, 跳过: 1 文件
```

---

## Phase 5：设计文档 + 测试

### 5.1 新增设计文档

`MCPK-v2.2-Design.md`：完整的 v2.2 规范，涵盖 AES-GCM 加密参数区格式、IntraRelationType 枚举、Group Index 扩展格式、JSON 索引 Schema。

### 5.2 新增测试用例

| 测试 ID | 名称 | 覆盖 |
|---------|------|------|
| test_21 | AES-GCM FULL 加密 roundtrip | Phase 1 |
| test_22 | AES-GCM METADATA_ONLY | Phase 1 |
| test_23 | AES-GCM DATA_ONLY | Phase 1 |
| test_24 | AES-GCM 错误密码 | Phase 1 |
| test_25 | AES-GCM 篡改检测 | Phase 1 |
| test_26 | XOR 向后兼容 | Phase 1 |
| test_27 | AES-GCM + 分组 + 关系 | Phase 1+2 |
| test_28 | 分组 Tag 基本 | Phase 2 |
| test_29 | 动态添加 Tag | Phase 2 |
| test_30 | 组内关系基本 | Phase 2 |
| test_31 | 多组内关系 | Phase 2 |
| test_32 | Tags + 组内关系 + 加密 | Phase 1+2 |
| test_33~35 | import_folder 变体 | Phase 3 |
| test_36~39 | JSON 索引打包变体 | Phase 4 |
| test_40~48 | inspect/tag/枚举/兼容性 | 综合 |
| test_49 | 同名文件区分 | group + index |

---

## 文件变更汇总

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `mcpk/constants.py` | 修改 | 新增 KdfType.PBKDF2_AES、IntraRelationType 枚举、ENCRYPTION_PARAMS_FMT_V2 |
| `mcpk/types.py` | 修改 | GroupEntry 增加 tags/intra_relations，新增 IntraRelation 类，EncryptionParams 扩展 |
| `mcpk/writer.py` | 修改 | AES-GCM 加密流程、import_folder()、load_index()、add_tag()、add_intra_relation() |
| `mcpk/reader.py` | 修改 | AES-GCM 解密流程、解析 tags/intra_relations |
| `mcpk/cli.py` | 修改 | --encryption/--auto-group/--index 参数，groups 命令展示 tags |
| `MCPK-v2.2-Design.md` | 新增 | v2.2 完整设计文档 |
| `test_mcpk.py` | 修改 | 新增 test_21 ~ test_32 |

---

## 依赖变化

```
# requirements.txt
cryptography>=41.0.0   # AES-256-GCM 支持（可选，XOR 模式零依赖）
zstd>=1.5.0            # 可选
lz4>=4.0.0             # 可选
```

运行时检测：`cryptography` 不可用时，`encryption="aes"` 自动回退 `encryption="xor"` 并打印警告。读取 AES 加密文件时若无 cryptography 则报错要求安装。
