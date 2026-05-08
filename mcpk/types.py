"""MCPK 数据类型定义。"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FileHeader:
    """MCPK 文件头 (64 字节)。"""
    magic: bytes = b"MCPK"
    version: int = 1
    flags: int = 0
    created_at: int = 0          # Unix ms
    toc_offset: int = 0
    toc_size: int = 0
    entry_count: int = 0
    data_section_size: int = 0


@dataclass
class TocEntry:
    """TOC 条目（单个文件的索引+元数据）。"""
    entry_type: int = 0x01       # EntryType
    compression: int = 0x00      # Compression
    crc32: int = 0
    created_at: int = 0          # Unix ms
    original_size: int = 0
    stored_size: int = 0
    blob_offset: int = 0
    name: str = ""
    mime_type: str = ""
    metadata: Optional[str] = None  # JSON string or None

    def metadata_dict(self) -> dict:
        """解析 metadata JSON 为 dict，无 metadata 则返回空 dict。"""
        if not self.metadata:
            return {}
        import json
        return json.loads(self.metadata)

    @property
    def compression_ratio(self) -> float:
        """压缩比（原始/存储）。"""
        if self.stored_size == 0:
            return 0.0
        return self.original_size / self.stored_size

    @property
    def is_compressed(self) -> bool:
        return self.compression != 0x00

    def __repr__(self) -> str:
        from .constants import EntryType, Compression
        try:
            type_name = EntryType(self.entry_type).name
        except ValueError:
            type_name = f"0x{self.entry_type:02x}"
        try:
            comp_name = Compression(self.compression).name
        except ValueError:
            comp_name = f"0x{self.compression:02x}"
        return (
            f"TocEntry({type_name}, name={self.name!r}, "
            f"mime={self.mime_type!r}, "
            f"original={self.original_size}, stored={self.stored_size}, "
            f"comp={comp_name}, crc32=0x{self.crc32:08x})"
        )
