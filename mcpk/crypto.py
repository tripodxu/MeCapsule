"""MCPK 共享加密和压缩工具。

writer.py 和 reader.py 共用的加密/解密/压缩/解压函数。
"""

import hashlib
import os
import struct

from .constants import (
    AES_GCM_NONCE_SIZE,
    PBKDF2_DEFAULT_ITERATIONS,
    Compression,
)

# ── 可选依赖：cryptography（AES-GCM）──────────────────────
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


# ── XOR 流加密 ────────────────────────────────────────────

def xor_bytes(data: bytes, key: bytes) -> bytes:
    """XOR 流加密/解密（与 1apluse xor_bytes 一致）。"""
    if not key:
        return data
    key_len = len(key)
    result = bytearray(data)
    for i in range(len(result)):
        result[i] ^= key[i % key_len]
    return bytes(result)


# ── XOR 模式密钥派生（v2.1 兼容）────────────────────────

def derive_key(password: str, salt: bytes) -> bytes:
    """从密码 + salt 派生 32 字节主密钥（XOR 模式）。"""
    pwd_bytes = password.encode("utf-8")
    seed = bytes(
        pwd_bytes[i % len(pwd_bytes)] ^ salt[i % len(salt)]
        for i in range(32)
    )
    return hashlib.sha256(seed + salt).digest()


def derive_control_key(master_key: bytes) -> bytes:
    """派生控制区加密密钥（XOR 模式）。"""
    return hashlib.sha256(master_key + b"ctrl").digest()


def derive_blob_key(master_key: bytes, entry_id: int, entry_salt: bytes) -> bytes:
    """派生单条目 blob 加密密钥（XOR 模式）。"""
    id_bytes = struct.pack("<I", entry_id)
    return hashlib.sha256(master_key + id_bytes + entry_salt).digest()


# ── AES-GCM 模式密钥派生（v2.2）──────────────────────────

def derive_key_pbkdf2(password: str, salt: bytes,
                      iterations: int = PBKDF2_DEFAULT_ITERATIONS) -> bytes:
    """PBKDF2-SHA256 派生 256-bit 主密钥。"""
    if not HAS_CRYPTO:
        raise ImportError("AES-GCM 加密需要 cryptography 库: pip install cryptography")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(password.encode("utf-8"))


def derive_subkeys_aes(master_key: bytes) -> tuple[bytes, bytes]:
    """从主密钥派生控制区密钥和数据区密钥基（HKDF-SHA256）。"""
    control_key = HKDF(
        algorithm=hashes.SHA256(), length=32,
        salt=None, info=b"mcpk-ctrl",
    ).derive(master_key)
    data_key_base = HKDF(
        algorithm=hashes.SHA256(), length=32,
        salt=None, info=b"mcpk-data",
    ).derive(master_key)
    return control_key, data_key_base


def derive_blob_key_aes(master_key: bytes, entry_id: int, entry_salt: bytes) -> bytes:
    """派生单条目 blob 加密密钥（AES 模式，HKDF）。"""
    id_bytes = struct.pack("<I", entry_id)
    return HKDF(
        algorithm=hashes.SHA256(), length=32,
        salt=entry_salt, info=b"mcpk-blob" + id_bytes,
    ).derive(master_key)


# ── AES-GCM 加密/解密 ────────────────────────────────────

def aes_gcm_encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """AES-256-GCM 加密。返回 nonce(12B) + ciphertext + tag(16B)。"""
    nonce = os.urandom(AES_GCM_NONCE_SIZE)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(nonce, plaintext, aad)
    return nonce + ct


def aes_gcm_decrypt(key: bytes, data: bytes, aad: bytes = b"") -> bytes:
    """AES-256-GCM 解密。输入 nonce(12B) + ciphertext + tag(16B)。"""
    nonce = data[:AES_GCM_NONCE_SIZE]
    ct = data[AES_GCM_NONCE_SIZE:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ct, aad)


# ── 可选压缩模块缓存 ──────────────────────────────────────
_zstd_mod = None
_lz4_mod = None
_zstd_checked = False
_lz4_checked = False


def get_zstd():
    global _zstd_mod, _zstd_checked
    if not _zstd_checked:
        _zstd_checked = True
        try:
            import zstd as _m
            _zstd_mod = _m
        except ImportError:
            pass
    return _zstd_mod


def get_lz4():
    global _lz4_mod, _lz4_checked
    if not _lz4_checked:
        _lz4_checked = True
        try:
            import lz4.frame as _m
            _lz4_mod = _m
        except ImportError:
            pass
    return _lz4_mod


def compress(data: bytes, compression: int) -> tuple[bytes, int]:
    """压缩数据。返回 (compressed_data, actual_compression)。

    zstd/lz4 不可用时回退到 zlib 并发出警告，返回的实际压缩算法为 ZLIB。
    """
    import zlib as _zlib
    import warnings

    if compression == Compression.NONE:
        return data, Compression.NONE
    elif compression == Compression.ZLIB:
        return _zlib.compress(data, level=6), Compression.ZLIB
    elif compression == Compression.ZSTD:
        m = get_zstd()
        if m is not None:
            return m.compress(data, 3), Compression.ZSTD
        warnings.warn("zstd 不可用，回退到 zlib 压缩", stacklevel=2)
        return _zlib.compress(data, level=6), Compression.ZLIB
    elif compression == Compression.LZ4:
        m = get_lz4()
        if m is not None:
            return m.compress(data), Compression.LZ4
        warnings.warn("lz4 不可用，回退到 zlib 压缩", stacklevel=2)
        return _zlib.compress(data, level=6), Compression.ZLIB
    else:
        raise ValueError(f"未知压缩算法: {compression}")


def decompress(data: bytes, compression: int) -> bytes:
    """解密数据。"""
    import zlib as _zlib

    if compression == Compression.NONE:
        return data
    elif compression == Compression.ZLIB:
        return _zlib.decompress(data)
    elif compression == Compression.ZSTD:
        m = get_zstd()
        if m is not None:
            return m.decompress(data)
        raise ImportError("数据使用 zstd 压缩，请安装 zstd: pip install zstd")
    elif compression == Compression.LZ4:
        m = get_lz4()
        if m is not None:
            return m.decompress(data)
        raise ImportError("数据使用 lz4 压缩，请安装 lz4: pip install lz4")
    else:
        raise ValueError(f"未知压缩算法: {compression}")
