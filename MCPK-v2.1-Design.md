# MCPK v2.1 增强方案：时间戳 + 加密

**基于 1apluse XOR 加密体系，适配 MCPK v2 布局**

> 两个核心增强：(1) 完整时间戳体系；(2) XOR 流加密，与 1apluse 的 magic + XOR 方案一脉相承。

---

## 1. 时间戳增强

### 1.1 现状

当前只有 `created_at`（Unix 毫秒），精度够但缺少修改时间和访问时间。

### 1.2 方案

在 TocEntry 中扩展时间戳字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `created_at` | uint64 (Unix ms) | 文件原始创建时间 |
| `modified_at` | uint64 (Unix ms) | 文件最后修改时间（从源文件 mtime 读取） |
| `packed_at` | uint64 (Unix ms) | 打包时间（写入 MCPK 的时刻） |

**Header 层面**增加 `packed_at`，记录整个容器的打包时刻。

**写入逻辑：**
```
created_at  = 源文件的 ctime (或用户指定)
modified_at = 源文件的 mtime (或用户指定)
packed_at   = time.time() * 1000 (打包瞬间)
```

**读取 API：**
```python
entry.created_at    # 源文件创建时间
entry.modified_at   # 源文件修改时间
entry.packed_at     # 打包时间
entry.time_info()   # 返回 dict，格式化为 ISO 8601
```

### 1.3 二进制布局变化

TocEntry 固定部分从 42 字节扩展到 58 字节：

```
原: type(B) + compression(B) + reserved(2s) + crc32(I)
    + created_at(Q) + original_size(Q) + stored_size(Q) + blob_offset(Q) + name_len(H)
    = 42 字节

新: type(B) + compression(B) + reserved(2s) + crc32(I)
    + created_at(Q) + modified_at(Q) + packed_at(Q)
    + original_size(Q) + stored_size(Q) + blob_offset(Q) + name_len(H)
    = 58 字节
```

Header 的 `created_at` 改为 `packed_at`（容器级打包时间）。

---

## 2. 加密方案（基于 1apluse XOR 体系）

### 2.1 设计理念

1apluse 的核心加密思路：
- **magic 字节**作为密钥种子（用户可配置，1-32 字节）
- **XOR 流加密**：`encrypted[i] = plain[i] ^ key[i % len(key)]`
- **密钥扩展**：短 magic 通过重复/衍生扩展为长密钥流

将这套思路适配到 MCPK v2 的分层布局上：

```
v2 布局天然分为三个控制区 + 一个数据区：

┌────────────────────┐
│  Header (64B)      │  ← 明文（必须保留，用于识别格式和定位加密区）
├────────────────────┤
│  Encryption Params │  ← 新增：加密参数区（salt、nonce 等）
├────────────────────┤
│  Magic Index       │  ← 可加密（隐藏文件类型）
├────────────────────┤
│  Data Section      │  ← 可加密（隐藏文件内容）
├────────────────────┤
│  Group Index       │  ← 可加密（隐藏分组结构）
├────────────────────┤
│  TOC               │  ← 可加密（隐藏文件名、元数据）
├────────────────────┤
│  Footer (16B)      │  ← 明文（从尾部定位）
└────────────────────┘
```

### 2.2 密钥派生（对齐 1apluse 风格）

```
用户密码 (string)
    │
    ▼
password_bytes = password.encode("utf-8")
    │
    ▼
┌─────────────────────────────────────────────────────┐
│ 密钥扩展算法（从 1apluse xor_bytes 演化）            │
│                                                     │
│ salt = 16 字节随机值 (存储在 Encryption Params 中)    │
│                                                     │
│ # 第一轮：密码 + salt 混合                           │
│ seed = bytes([                                     │
│     password_bytes[i % len(password_bytes)]         │
│     ^ salt[i % len(salt)]                          │
│     for i in range(32)                             │
│ ])                                                  │
│                                                     │
│ # 第二轮：SHA-256 固化为 32 字节主密钥                │
│ master_key = SHA256(seed + salt)                    │
│                                                     │
│ # 子密钥派生                                        │
│ control_key = SHA256(master_key + b"ctrl")[0:32]    │
│ blob_key_base = SHA256(master_key + b"blob")[0:32]  │
└─────────────────────────────────────────────────────┘
    │
    ▼
master_key (32 字节) → 控制区密钥 + 数据区密钥基
```

> 相比 1apluse 直接用 magic 做 XOR key，这里增加了 salt 和 SHA-256 固化，
> 但保留了 XOR 作为核心加密原语，与 1apluse 一脉相承。

### 2.3 XOR 流加密（与 1apluse xor_bytes 一致）

```python
def xor_bytes(data: bytes, key: bytes) -> bytes:
    """与 1apluse 完全一致的 XOR 加密。"""
    if not key:
        return data
    return bytes([b ^ key[i % len(key)] for i, b in enumerate(data)])
```

**控制区加密：**
```
encrypted_magic_index  = xor_bytes(raw_magic_index,  control_key)
encrypted_group_index  = xor_bytes(raw_group_index,  control_key)
encrypted_toc          = xor_bytes(raw_toc,          control_key)
```

**数据区加密（每条目独立密钥）：**
```
blob_key_i = SHA256(blob_key_base + entry_id_bytes + entry_salt_i)[0:32]
encrypted_blob_i = xor_bytes(blob_data_i, blob_key_i)
```

### 2.4 Encryption Params 区（新增）

位于 Header 之后、Magic Index 之前。

```
偏移   大小    类型        字段            说明
──────────────────────────────────────────────────────────────
0x00   4B     char[4]     params_magic    "ENC0"
0x04   1B     uint8_t     kdf_type        0x01=SHA256_XOR
0x05   1B     uint8_t     encrypt_mode    加密模式（见下表）
0x06   2B     uint16_le   reserved        保留
0x08   16B    bytes       salt            KDF 盐值（随机生成）
0x18   32B    bytes       control_key_hash  SHA256(control_key) 用于验证密码
──────────────────────────────────────────────────────────────
        56B 总计
```

**encrypt_mode 枚举：**

| 值 | 名称 | 说明 |
|----|------|------|
| 0x00 | NONE | 不加密（明文） |
| 0x01 | FULL | 控制区 + 数据区全部加密 |
| 0x02 | METADATA_ONLY | 仅加密控制区（Magic Index + Group Index + TOC） |
| 0x03 | DATA_ONLY | 仅加密数据区（Blob） |

**验证密码流程：**
```
1. 读取 salt 和 control_key_hash
2. 用户输入密码 → KDF → master_key → control_key
3. SHA256(control_key) == control_key_hash ? 密码正确 : 密码错误
```

### 2.5 加密后的文件布局

```
┌────────────────────────────────┐
│  File Header (64 B)            │  明文, flags.ENCRYPTED=1
├────────────────────────────────┤
│  Encryption Params (56 B)      │  明文（含 salt + key_hash）
├────────────────────────────────┤
│  Magic Index (加密)            │  XOR(control_key)
├────────────────────────────────┤
│  Data Section                  │  XOR(blob_key_i) per entry
│    [Group 0 Blobs]             │
│    [Group 1 Blobs]             │
│    [Ungrouped Blobs]           │
├────────────────────────────────┤
│  Group Index (加密)            │  XOR(control_key)
├────────────────────────────────┤
│  TOC (加密)                    │  XOR(control_key)
├────────────────────────────────┤
│  Footer (16 B)                 │  明文 (toc_offset 指向加密后的 TOC)
└────────────────────────────────┘
```

### 2.6 读取流程（加密文件）

```
1. 读取 Footer → toc_offset
2. 读取 Header → flags & ENCRYPTED ?
3. 如果加密：
   a. 读取 Encryption Params (56B)
   b. 提示用户输入密码
   c. KDF(password, salt) → master_key → control_key
   d. SHA256(control_key) == control_key_hash ? 继续 : 报错
4. 读取加密的 Magic Index → XOR(control_key) → 解密 → 解析
5. 读取加密的 Group Index → XOR(control_key) → 解密 → 解析
6. 读取加密的 TOC → XOR(control_key) → 解密 → 解析
7. 提取文件时：
   a. 从 TOC 获取 blob_offset, stored_size
   b. 读取加密 blob
   c. blob_key_i = KDF_derive(master_key, entry_id, entry_salt)
   d. XOR(blob_key_i) → 解密 → 解压 → CRC32 校验
```

### 2.7 安全性分析

| 维度 | 1apluse | MCPK v2.1 | 说明 |
|------|---------|-----------|------|
| 加密原语 | XOR | XOR | 一致 |
| 密钥来源 | magic (直接使用) | password + salt + SHA-256 | MCPK 增加了 KDF |
| 抗暴力 | 依赖 magic 熵 | 依赖密码强度 + 128-bit salt | salt 防彩虹表 |
| 密钥验证 | 无 | SHA256(control_key) hash | 可快速验证密码 |
| 分层加密 | 无 | FULL / METADATA / DATA 三种模式 | 更灵活 |
| 每条目密钥 | 无 | entry_id + entry_salt 衍生 | 条目间密钥隔离 |

**与 1apluse 的兼容点：**
- 核心加密都是 `xor_bytes(data, key)`
- 密钥都是字节序列，长度 1-32 字节
- 思路一致：magic/密码 → 密钥 → XOR 数据

**比 1apluse 增强的点：**
- 加了 salt，同一密码不同文件产生不同密钥
- 加了 SHA-256 KDF，密码不会直接暴露为 XOR key
- 加了密钥验证 hash，可以判断密码是否正确
- 分层加密，可选择只加密元数据或只加密数据

---

## 3. API 设计

### 3.1 写入（加密）

```python
from mcpk import MCPKWriter

# 基本加密（全量加密）
with MCPKWriter("secret.mcpk", password="mypassword") as w:
    w.add_file("report.pdf")
    w.add_file("photo.jpg", group_name="相册")

# 仅加密元数据（快速加密，隐藏文件名/类型/分组）
with MCPKWriter("obscured.mcpk", password="mypassword",
                encrypt_mode="metadata_only") as w:
    w.add_file("video.mp4")

# 不加密（默认行为，与现有兼容）
with MCPKWriter("plain.mcpk") as w:
    w.add_file("readme.md")
```

### 3.2 读取（解密）

```python
from mcpk import MCPKReader

# 自动检测是否加密
with MCPKReader("secret.mcpk", password="mypassword") as r:
    for entry in r.entries:
        print(entry.name, entry.created_at, entry.modified_at)
    data = r.extract("report.pdf")

# 密码错误会抛出明确异常
try:
    with MCPKReader("secret.mcpk", password="wrong") as r:
        pass
except MCPKError as e:
    print(f"解密失败: {e}")  # "密码错误或文件已损坏"
```

### 3.3 时间戳 API

```python
entry.created_at    # int, Unix ms, 源文件创建时间
entry.modified_at   # int, Unix ms, 源文件修改时间
entry.packed_at     # int, Unix ms, 打包时间

entry.time_info()   # dict:
# {
#   "created": "2026-05-08T14:30:00+08:00",
#   "modified": "2026-05-08T15:00:00+08:00",
#   "packed": "2026-05-08T16:00:00+08:00",
# }
```

### 3.4 CLI

```bash
# 加密打包
python -m mcpk pack file1.mp4 file2.srt -o secret.mcpk --password "mypass"

# 仅加密元数据
python -m mcpk pack file1.mp4 -o obscured.mcpk --password "mypass" --encrypt-mode metadata

# 加密读取（自动提示密码）
python -m mcpk list secret.mcpk --password "mypass"
python -m mcpk extract secret.mcpk -o ./out/ --password "mypass"

# 检查加密状态
python -m mcpk inspect secret.mcpk
# → 输出中会显示 "encrypted": true, "encrypt_mode": "FULL"
```

---

## 4. 向后兼容

| 场景 | 处理 |
|------|------|
| v2.0 reader 读 v2.1 未加密文件 | 正常读取（time 字段新增但旧 reader 跳过 reserved） |
| v2.0 reader 读 v2.1 加密文件 | 检测到 ENCRYPTED flag，报错提示升级工具 |
| v2.1 reader 读 v2.0 文件 | 正常读取（无 Encryption Params 区，视为未加密） |
| v2.1 reader 读 v1 文件 | 正常读取（已有 v1 兼容逻辑） |

---

## 5. 实现优先级

| 阶段 | 内容 | 说明 |
|------|------|------|
| Phase 1 | 时间戳扩展 | TocEntry 增加 modified_at / packed_at |
| Phase 2 | XOR 加密核心 | xor_bytes + KDF + Encryption Params |
| Phase 3 | Writer/Reader 加密集成 | password 参数、加密/解密流程 |
| Phase 4 | CLI 加密支持 | --password / --encrypt-mode |
| Phase 5 | 测试 | 加密 roundtrip、密码错误、分层加密 |
