"""MCPK 格式常量和枚举定义。"""

import struct
from enum import IntEnum

# ── 魔数和版本 ──────────────────────────────────────────────
MAGIC = b"MCPK"
VERSION = 1

# ── 固定大小 ────────────────────────────────────────────────
HEADER_SIZE = 64
FOOTER_SIZE = 16

# ── struct 格式 ─────────────────────────────────────────────
# Header: magic(4s) + version(H) + flags(H) + created_at(Q)
#         + toc_offset(Q) + toc_size(Q) + entry_count(I)
#         + data_section_size(I) + reserved(24s)
HEADER_FMT = "<4sHH Q Q Q I I 24s"
HEADER_SIZE_CALC = struct.calcsize(HEADER_FMT)  # 应为 64

# Footer: magic(4s) + toc_offset(Q) + footer_crc(I)
FOOTER_FMT = "<4sQI"
FOOTER_SIZE_CALC = struct.calcsize(FOOTER_FMT)  # 应为 16

# TOC Entry 固定部分: type(B) + compression(B) + reserved(2s)
#   + crc32(I) + created_at(Q) + original_size(Q)
#   + stored_size(Q) + blob_offset(Q) + name_len(H)
TOC_ENTRY_FIXED_FMT = "<BB2sI Q Q Q Q H"
TOC_ENTRY_FIXED_SIZE = struct.calcsize(TOC_ENTRY_FIXED_FMT)  # 42


class EntryType(IntEnum):
    """条目类型枚举。"""
    DOCUMENT = 0x01
    IMAGE    = 0x02
    AUDIO    = 0x03


class Compression(IntEnum):
    """压缩算法枚举。"""
    NONE = 0x00
    ZLIB = 0x01
    ZSTD = 0x02
    LZ4  = 0x03


# ── 扩展名 → (EntryType, MIME, Compression) 映射 ─────────
EXTENSION_MAP = {
    # 文档
    ".md":   (EntryType.DOCUMENT, "text/markdown",      Compression.ZLIB),
    ".txt":  (EntryType.DOCUMENT, "text/plain",          Compression.ZLIB),
    ".json": (EntryType.DOCUMENT, "application/json",    Compression.ZLIB),
    ".csv":  (EntryType.DOCUMENT, "text/csv",            Compression.ZLIB),
    ".html": (EntryType.DOCUMENT, "text/html",           Compression.ZLIB),
    ".htm":  (EntryType.DOCUMENT, "text/html",           Compression.ZLIB),
    ".pdf":  (EntryType.DOCUMENT, "application/pdf",     Compression.NONE),
    ".docx": (EntryType.DOCUMENT, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", Compression.NONE),
    ".doc":  (EntryType.DOCUMENT, "application/msword",  Compression.NONE),
    ".xlsx": (EntryType.DOCUMENT, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", Compression.NONE),
    ".pptx": (EntryType.DOCUMENT, "application/vnd.openxmlformats-officedocument.presentationml.presentation", Compression.NONE),
    ".xml":  (EntryType.DOCUMENT, "application/xml",     Compression.ZLIB),
    ".yaml": (EntryType.DOCUMENT, "application/x-yaml",  Compression.ZLIB),
    ".yml":  (EntryType.DOCUMENT, "application/x-yaml",  Compression.ZLIB),

    # 图片
    ".jpg":  (EntryType.IMAGE, "image/jpeg",    Compression.NONE),
    ".jpeg": (EntryType.IMAGE, "image/jpeg",    Compression.NONE),
    ".png":  (EntryType.IMAGE, "image/png",     Compression.NONE),
    ".gif":  (EntryType.IMAGE, "image/gif",     Compression.NONE),
    ".webp": (EntryType.IMAGE, "image/webp",    Compression.NONE),
    ".bmp":  (EntryType.IMAGE, "image/bmp",     Compression.ZLIB),
    ".tiff": (EntryType.IMAGE, "image/tiff",    Compression.ZLIB),
    ".tif":  (EntryType.IMAGE, "image/tiff",    Compression.ZLIB),
    ".svg":  (EntryType.IMAGE, "image/svg+xml", Compression.ZLIB),
    ".ico":  (EntryType.IMAGE, "image/x-icon",  Compression.NONE),

    # 音频
    ".mp3":  (EntryType.AUDIO, "audio/mpeg",  Compression.NONE),
    ".wav":  (EntryType.AUDIO, "audio/wav",   Compression.ZSTD),
    ".ogg":  (EntryType.AUDIO, "audio/ogg",   Compression.NONE),
    ".flac": (EntryType.AUDIO, "audio/flac",  Compression.NONE),
    ".aac":  (EntryType.AUDIO, "audio/aac",   Compression.NONE),
    ".m4a":  (EntryType.AUDIO, "audio/mp4",   Compression.NONE),
    ".wma":  (EntryType.AUDIO, "audio/x-ms-wma", Compression.NONE),
}


# ── 全局标志位 ──────────────────────────────────────────────
FLAG_ENCRYPTED = 0x01
FLAG_SIGNED    = 0x02
