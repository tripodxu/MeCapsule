"""
MCPK - MeCapsule Package
自定义二进制容器格式，用于个人知识管理。
"""

from .constants import MAGIC, VERSION, EntryType, Compression
from .types import TocEntry, FileHeader
from .writer import MCPKWriter
from .reader import MCPKReader

__version__ = "1.0.0"
__all__ = [
    "MAGIC", "VERSION", "EntryType", "Compression",
    "TocEntry", "FileHeader",
    "MCPKWriter", "MCPKReader",
]
