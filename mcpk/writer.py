"""MCPK 文件写入工具。"""

from __future__ import annotations

import binascii
import json
import os
import struct
import time
import zlib
from pathlib import Path
from typing import Optional, Union

from .constants import (
    MAGIC, VERSION, HEADER_SIZE, FOOTER_SIZE,
    HEADER_FMT, FOOTER_FMT, TOC_ENTRY_FIXED_FMT,
    EntryType, Compression, EXTENSION_MAP,
)
from .types import FileHeader, TocEntry


class MCPKWriter:
    """
    MCPK 文件写入器。

    用法:
        with MCPKWriter("output.mcpk") as writer:
            writer.add_file("report.pdf", metadata={"title": "报告"})
            writer.add_file("photo.jpg")
            writer.add_directory("./documents/")
    """

    def __init__(self, output_path: Union[str, Path]):
        self.output_path = Path(output_path)
        self._file = None
        self._entries: list[TocEntry] = []
        self._cursor = HEADER_SIZE  # 数据从 header 之后开始
        self._closed = False

    def __enter__(self):
        self._file = open(self.output_path, "wb")
        # 写入占位 header
        self._file.write(b"\x00" * HEADER_SIZE)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self._closed:
            self.finalize()

    # ── 公开 API ──────────────────────────────────────────

    def add_file(
        self,
        file_path: Union[str, Path],
        *,
        arcname: Optional[str] = None,
        entry_type: Optional[int] = None,
        mime_type: Optional[str] = None,
        compression: Optional[int] = None,
        metadata: Optional[dict] = None,
        created_at: Optional[int] = None,
    ) -> TocEntry:
        """
        添加单个文件到 MCPK 包。

        Args:
            file_path: 源文件路径
            arcname: 包内文件名（默认使用原始文件名）
            entry_type: 条目类型（自动推断）
            mime_type: MIME 类型（自动推断）
            compression: 压缩算法（自动推断）
            metadata: 附加元数据 dict
            created_at: 创建时间戳 ms（默认使用当前时间）

        Returns:
            写入的 TocEntry
        """
        file_path = Path(file_path)
        if not file_path.is_file():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        # 自动推断类型和压缩
        ext = file_path.suffix.lower()
        inferred = EXTENSION_MAP.get(ext)

        if entry_type is None:
            entry_type = inferred[0] if inferred else EntryType.DOCUMENT
        if mime_type is None:
            mime_type = inferred[1] if inferred else "application/octet-stream"
        if compression is None:
            compression = inferred[2] if inferred else Compression.ZLIB

        name = arcname or file_path.name

        # 安全检查：路径穿越
        if ".." in name or name.startswith("/") or name.startswith("\\"):
            raise ValueError(f"不安全的文件名: {name}")

        # 读取原始数据
        original_data = file_path.read_bytes()
        original_size = len(original_data)

        # 压缩
        stored_data = self._compress(original_data, compression)
        stored_size = len(stored_data)

        # CRC32 (对原始数据计算)
        crc32_val = binascii.crc32(original_data) & 0xFFFFFFFF

        # 时间戳
        ts = created_at or int(time.time() * 1000)

        # 构建 metadata JSON
        meta_json = None
        if metadata:
            # 自动补充 title
            if "title" not in metadata:
                metadata["title"] = file_path.stem
            meta_json = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))

        # 创建 TOC 条目
        entry = TocEntry(
            entry_type=entry_type,
            compression=compression,
            crc32=crc32_val,
            created_at=ts,
            original_size=original_size,
            stored_size=stored_size,
            blob_offset=self._cursor,
            name=name,
            mime_type=mime_type,
            metadata=meta_json,
        )

        # 写入 blob
        self._file.write(stored_data)
        self._cursor += stored_size
        self._entries.append(entry)

        return entry

    def add_data(
        self,
        data: bytes,
        name: str,
        *,
        entry_type: int = EntryType.DOCUMENT,
        mime_type: str = "application/octet-stream",
        compression: int = Compression.ZLIB,
        metadata: Optional[dict] = None,
        created_at: Optional[int] = None,
    ) -> TocEntry:
        """
        直接添加原始字节数据。

        Args:
            data: 原始字节数据
            name: 包内文件名
            entry_type: 条目类型
            mime_type: MIME 类型
            compression: 压缩算法
            metadata: 附加元数据 dict
            created_at: 创建时间戳 ms

        Returns:
            写入的 TocEntry
        """
        if ".." in name or name.startswith("/") or name.startswith("\\"):
            raise ValueError(f"不安全的文件名: {name}")

        original_size = len(data)
        stored_data = self._compress(data, compression)
        stored_size = len(stored_data)
        crc32_val = binascii.crc32(data) & 0xFFFFFFFF
        ts = created_at or int(time.time() * 1000)

        meta_json = None
        if metadata:
            if "title" not in metadata:
                metadata["title"] = Path(name).stem
            meta_json = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))

        entry = TocEntry(
            entry_type=entry_type,
            compression=compression,
            crc32=crc32_val,
            created_at=ts,
            original_size=original_size,
            stored_size=stored_size,
            blob_offset=self._cursor,
            name=name,
            mime_type=mime_type,
            metadata=meta_json,
        )

        self._file.write(stored_data)
        self._cursor += stored_size
        self._entries.append(entry)
        return entry

    def add_directory(
        self,
        dir_path: Union[str, Path],
        *,
        recursive: bool = True,
        prefix: str = "",
        metadata_fn=None,
    ) -> list[TocEntry]:
        """
        添加整个目录。

        Args:
            dir_path: 目录路径
            recursive: 是否递归子目录
            prefix: 包内路径前缀
            metadata_fn: 可选的回调 fn(file_path) -> dict，为每个文件生成元数据

        Returns:
            写入的 TocEntry 列表
        """
        dir_path = Path(dir_path)
        if not dir_path.is_dir():
            raise NotADirectoryError(f"不是目录: {dir_path}")

        entries = []
        pattern = "**/*" if recursive else "*"
        for f in sorted(dir_path.glob(pattern)):
            if not f.is_file():
                continue
            # 计算包内名称
            relative = f.relative_to(dir_path)
            arcname = f"{prefix}{relative}" if prefix else str(relative)
            arcname = arcname.replace("\\", "/")  # 统一路径分隔符

            meta = metadata_fn(f) if metadata_fn else None
            entry = self.add_file(f, arcname=arcname, metadata=meta)
            entries.append(entry)

        return entries

    def finalize(self):
        """
        完成写入：写入 TOC 和 Footer，回写 Header。
        """
        if self._closed:
            return
        self._closed = True

        toc_offset = self._cursor

        # 写入 TOC
        toc_bytes = self._build_toc()
        self._file.write(toc_bytes)
        toc_size = len(toc_bytes)

        # 写入 Footer
        footer_data = struct.pack(
            FOOTER_FMT,
            MAGIC,
            toc_offset,
            0,  # placeholder
        )
        footer_crc = binascii.crc32(footer_data[:12]) & 0xFFFFFFFF
        footer_data = struct.pack(
            FOOTER_FMT,
            MAGIC,
            toc_offset,
            footer_crc,
        )
        self._file.write(footer_data)

        # 计算数据区大小
        data_section_size = toc_offset - HEADER_SIZE

        # 回写 Header
        created_at = self._entries[0].created_at if self._entries else int(time.time() * 1000)
        header = struct.pack(
            HEADER_FMT,
            MAGIC,
            VERSION,
            0,  # flags
            created_at,
            toc_offset,
            toc_size,
            len(self._entries),
            data_section_size,
            b"\x00" * 24,  # reserved
        )
        self._file.seek(0)
        self._file.write(header)
        self._file.close()

    # ── 内部方法 ──────────────────────────────────────────

    def _compress(self, data: bytes, compression: int) -> bytes:
        """按指定算法压缩数据。"""
        if compression == Compression.NONE:
            return data
        elif compression == Compression.ZLIB:
            return zlib.compress(data, level=6)
        elif compression == Compression.ZSTD:
            try:
                import zstd
                return zstd.compress(data, 3)
            except ImportError:
                # zstd 不可用，回退到 zlib
                return zlib.compress(data, level=6)
        elif compression == Compression.LZ4:
            try:
                import lz4.frame
                return lz4.frame.compress(data)
            except ImportError:
                return zlib.compress(data, level=6)
        else:
            raise ValueError(f"未知压缩算法: {compression}")

    def _build_toc(self) -> bytes:
        """序列化所有 TOC 条目为 bytes。"""
        parts = []
        for entry in self._entries:
            name_bytes = entry.name.encode("utf-8")
            mime_bytes = entry.mime_type.encode("utf-8")
            meta_bytes = entry.metadata.encode("utf-8") if entry.metadata else b""

            # 固定部分
            fixed = struct.pack(
                TOC_ENTRY_FIXED_FMT,
                entry.entry_type,
                entry.compression,
                b"\x00\x00",  # reserved
                entry.crc32,
                entry.created_at,
                entry.original_size,
                entry.stored_size,
                entry.blob_offset,
                len(name_bytes),
            )
            parts.append(fixed)
            parts.append(name_bytes)
            parts.append(struct.pack("<H", len(mime_bytes)))
            parts.append(mime_bytes)
            parts.append(struct.pack("<H", len(meta_bytes)))
            if meta_bytes:
                parts.append(meta_bytes)

        return b"".join(parts)

    @property
    def entries(self) -> list[TocEntry]:
        """当前已添加的条目列表（只读副本）。"""
        return list(self._entries)
