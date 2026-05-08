"""MCPK 文件读取工具。"""

from __future__ import annotations

import binascii
import json
import struct
import zlib
from pathlib import Path
from typing import Optional, Union

from .constants import (
    MAGIC, VERSION, HEADER_SIZE, FOOTER_SIZE,
    HEADER_FMT, FOOTER_FMT, TOC_ENTRY_FIXED_FMT,
    EntryType, Compression,
)
from .types import FileHeader, TocEntry


class MCPKError(Exception):
    """MCPK 格式相关错误。"""
    pass


class MCPKReader:
    """
    MCPK 文件读取器。

    用法:
        with MCPKReader("archive.mcpk") as reader:
            # 列出所有条目
            for entry in reader.entries:
                print(entry)

            # 提取单个文件
            data = reader.extract("report.pdf")
            reader.extract_to("photo.jpg", "./output/")

            # 提取全部
            reader.extract_all("./output/")
    """

    def __init__(self, file_path: Union[str, Path]):
        self.file_path = Path(file_path)
        self._file = None
        self._header: Optional[FileHeader] = None
        self._entries: list[TocEntry] = []
        self._loaded = False

    def __enter__(self):
        self._file = open(self.file_path, "rb")
        self._load()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._file:
            self._file.close()

    # ── 属性 ──────────────────────────────────────────────

    @property
    def header(self) -> FileHeader:
        """文件头信息。"""
        self._ensure_loaded()
        return self._header

    @property
    def entries(self) -> list[TocEntry]:
        """所有 TOC 条目。"""
        self._ensure_loaded()
        return list(self._entries)

    @property
    def entry_count(self) -> int:
        """条目总数。"""
        return len(self._entries)

    # ── 公开 API ──────────────────────────────────────────

    def list_entries(self, entry_type: Optional[int] = None) -> list[TocEntry]:
        """
        列出条目，可按类型过滤。

        Args:
            entry_type: 过滤的条目类型 (EntryType 枚举值)

        Returns:
            过滤后的 TocEntry 列表
        """
        if entry_type is None:
            return self.entries
        return [e for e in self._entries if e.entry_type == entry_type]

    def find(self, name: str) -> Optional[TocEntry]:
        """
        按文件名查找条目。

        Args:
            name: 文件名

        Returns:
            匹配的 TocEntry，未找到返回 None
        """
        for entry in self._entries:
            if entry.name == name:
                return entry
        return None

    def extract(self, name: str) -> bytes:
        """
        提取指定文件的原始数据（解压后）。

        Args:
            name: 文件名

        Returns:
            原始文件字节数据

        Raises:
            KeyError: 文件名不存在
        """
        entry = self.find(name)
        if entry is None:
            raise KeyError(f"文件不存在: {name}")
        return self.extract_entry(entry)

    def extract_entry(self, entry: TocEntry) -> bytes:
        """
        提取指定条目的原始数据。

        Args:
            entry: TocEntry 对象

        Returns:
            原始文件字节数据

        Raises:
            MCPKError: 数据损坏
        """
        # 定位并读取 blob
        self._file.seek(entry.blob_offset)
        stored_data = self._file.read(entry.stored_size)

        if len(stored_data) != entry.stored_size:
            raise MCPKError(
                f"读取数据不完整: {entry.name} "
                f"(期望 {entry.stored_size}, 实际 {len(stored_data)})"
            )

        # 解压
        original_data = self._decompress(stored_data, entry.compression)

        # 校验大小
        if len(original_data) != entry.original_size:
            raise MCPKError(
                f"数据大小不匹配: {entry.name} "
                f"(期望 {entry.original_size}, 实际 {len(original_data)})"
            )

        # 校验 CRC32
        crc32_val = binascii.crc32(original_data) & 0xFFFFFFFF
        if crc32_val != entry.crc32:
            raise MCPKError(
                f"CRC32 校验失败: {entry.name} "
                f"(期望 0x{entry.crc32:08x}, 实际 0x{crc32_val:08x})"
            )

        return original_data

    def extract_to(
        self,
        name: str,
        output_dir: Union[str, Path],
        *,
        preserve_structure: bool = True,
    ) -> Path:
        """
        提取指定文件到目录。

        Args:
            name: 文件名
            output_dir: 输出目录
            preserve_structure: 是否保留包内目录结构

        Returns:
            输出文件的 Path
        """
        data = self.extract(name)
        output_dir = Path(output_dir)

        if preserve_structure:
            out_path = output_dir / name
        else:
            out_path = output_dir / Path(name).name

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        return out_path

    def extract_all(
        self,
        output_dir: Union[str, Path],
        *,
        preserve_structure: bool = True,
    ) -> list[Path]:
        """
        提取全部文件。

        Args:
            output_dir: 输出目录
            preserve_structure: 是否保留包内目录结构

        Returns:
            输出文件路径列表
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        paths = []
        for entry in self._entries:
            p = self.extract_to(entry.name, output_dir, preserve_structure=preserve_structure)
            paths.append(p)
        return paths

    def get_metadata(self, name: str) -> dict:
        """
        获取指定文件的元数据。

        Args:
            name: 文件名

        Returns:
            元数据 dict

        Raises:
            KeyError: 文件名不存在
        """
        entry = self.find(name)
        if entry is None:
            raise KeyError(f"文件不存在: {name}")
        return entry.metadata_dict()

    def verify(self) -> list[str]:
        """
        验证整个文件的完整性。

        Returns:
            错误消息列表（空列表表示完全通过）
        """
        errors = []

        # 验证 Header
        if self._header.magic != MAGIC:
            errors.append(f"Header magic 不匹配: {self._header.magic!r}")

        if self._header.version > VERSION:
            errors.append(
                f"版本过高: {self._header.version} (当前工具支持 {VERSION})"
            )

        # 验证 Footer
        try:
            self._file.seek(-FOOTER_SIZE, 2)
            footer_data = self._file.read(FOOTER_SIZE)
            footer_magic, footer_toc_offset, footer_crc = struct.unpack(
                FOOTER_FMT, footer_data
            )
            if footer_magic != MAGIC:
                errors.append(f"Footer magic 不匹配: {footer_magic!r}")
            computed_crc = binascii.crc32(footer_data[:12]) & 0xFFFFFFFF
            if computed_crc != footer_crc:
                errors.append(
                    f"Footer CRC 不匹配 (期望 0x{footer_crc:08x}, "
                    f"实际 0x{computed_crc:08x})"
                )
        except Exception as e:
            errors.append(f"Footer 读取失败: {e}")

        # 验证每个条目
        for i, entry in enumerate(self._entries):
            try:
                data = self.extract_entry(entry)
            except MCPKError as e:
                errors.append(f"条目 [{i}] {entry.name}: {e}")

        return errors

    def inspect(self) -> dict:
        """
        返回文件的详细检查信息。

        Returns:
            包含 header、entries、统计信息的 dict
        """
        h = self._header
        entries_info = []
        total_original = 0
        total_stored = 0

        for e in self._entries:
            total_original += e.original_size
            total_stored += e.stored_size
            entries_info.append({
                "name": e.name,
                "type": EntryType(e.entry_type).name if e.entry_type in EntryType._value2member_map_ else f"0x{e.entry_type:02x}",
                "mime": e.mime_type,
                "compression": Compression(e.compression).name if e.compression in Compression._value2member_map_ else f"0x{e.compression:02x}",
                "original_size": e.original_size,
                "stored_size": e.stored_size,
                "ratio": f"{e.compression_ratio:.2f}x" if e.stored_size > 0 else "N/A",
                "crc32": f"0x{e.crc32:08x}",
                "metadata": e.metadata_dict(),
            })

        return {
            "file": str(self.file_path),
            "file_size": self.file_path.stat().st_size,
            "version": h.version,
            "flags": h.flags,
            "created_at": h.created_at,
            "entry_count": h.entry_count,
            "toc_offset": h.toc_offset,
            "toc_size": h.toc_size,
            "data_section_size": h.data_section_size,
            "total_original_size": total_original,
            "total_stored_size": total_stored,
            "overall_ratio": f"{total_original / total_stored:.2f}x" if total_stored > 0 else "N/A",
            "entries": entries_info,
        }

    # ── 内部方法 ──────────────────────────────────────────

    def _ensure_loaded(self):
        """确保文件已加载。"""
        if not self._loaded:
            raise MCPKError("文件未打开，请使用 with 语句")

    def _load(self):
        """加载并解析文件。"""
        file_size = self.file_path.stat().st_size

        if file_size < HEADER_SIZE + FOOTER_SIZE:
            raise MCPKError("文件太小，不是有效的 MCPK 文件")

        # 读取 Header
        self._file.seek(0)
        header_data = self._file.read(HEADER_SIZE)
        (magic, version, flags, created_at,
         toc_offset, toc_size, entry_count,
         data_section_size, _reserved) = struct.unpack(HEADER_FMT, header_data)

        if magic != MAGIC:
            raise MCPKError(f"不是有效的 MCPK 文件 (magic: {magic!r})")

        if version > VERSION:
            raise MCPKError(
                f"不支持的版本 {version}，当前工具最高支持 v{VERSION}"
            )

        self._header = FileHeader(
            magic=magic,
            version=version,
            flags=flags,
            created_at=created_at,
            toc_offset=toc_offset,
            toc_size=toc_size,
            entry_count=entry_count,
            data_section_size=data_section_size,
        )

        # 读取 TOC
        self._file.seek(toc_offset)
        toc_data = self._file.read(toc_size)
        self._entries = self._parse_toc(toc_data, entry_count)
        self._loaded = True

    def _parse_toc(self, toc_data: bytes, expected_count: int) -> list[TocEntry]:
        """解析 TOC 数据为 TocEntry 列表。"""
        entries = []
        offset = 0

        for i in range(expected_count):
            if offset + 40 > len(toc_data):
                raise MCPKError(f"TOC 数据不完整 (条目 {i}/{expected_count})")

            # 读取固定部分
            (entry_type, compression, _reserved, crc32,
             created_at, original_size, stored_size,
             blob_offset, name_len) = struct.unpack_from(
                TOC_ENTRY_FIXED_FMT, toc_data, offset
            )
            offset += struct.calcsize(TOC_ENTRY_FIXED_FMT)

            # 读取 name
            if offset + name_len > len(toc_data):
                raise MCPKError(f"TOC name 数据越界 (条目 {i})")
            name = toc_data[offset:offset + name_len].decode("utf-8")
            offset += name_len

            # 读取 mime_len + mime
            if offset + 2 > len(toc_data):
                raise MCPKError(f"TOC mime_len 数据越界 (条目 {i})")
            mime_len = struct.unpack_from("<H", toc_data, offset)[0]
            offset += 2
            if offset + mime_len > len(toc_data):
                raise MCPKError(f"TOC mime 数据越界 (条目 {i})")
            mime_type = toc_data[offset:offset + mime_len].decode("utf-8")
            offset += mime_len

            # 读取 meta_len + metadata
            if offset + 2 > len(toc_data):
                raise MCPKError(f"TOC meta_len 数据越界 (条目 {i})")
            meta_len = struct.unpack_from("<H", toc_data, offset)[0]
            offset += 2
            metadata = None
            if meta_len > 0:
                if offset + meta_len > len(toc_data):
                    raise MCPKError(f"TOC metadata 数据越界 (条目 {i})")
                metadata = toc_data[offset:offset + meta_len].decode("utf-8")
                offset += meta_len

            entry = TocEntry(
                entry_type=entry_type,
                compression=compression,
                crc32=crc32,
                created_at=created_at,
                original_size=original_size,
                stored_size=stored_size,
                blob_offset=blob_offset,
                name=name,
                mime_type=mime_type,
                metadata=metadata,
            )
            entries.append(entry)

        return entries

    def _decompress(self, data: bytes, compression: int) -> bytes:
        """按指定算法解压数据。"""
        if compression == Compression.NONE:
            return data
        elif compression == Compression.ZLIB:
            return zlib.decompress(data)
        elif compression == Compression.ZSTD:
            try:
                import zstd
                return zstd.decompress(data)
            except ImportError:
                raise MCPKError("数据使用 zstd 压缩，请安装 zstd: pip install zstd")
        elif compression == Compression.LZ4:
            try:
                import lz4.frame
                return lz4.frame.decompress(data)
            except ImportError:
                raise MCPKError("数据使用 lz4 压缩，请安装 lz4: pip install lz4")
        else:
            raise MCPKError(f"未知压缩算法: {compression}")
