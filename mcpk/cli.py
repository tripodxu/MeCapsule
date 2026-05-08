"""MCPK 命令行工具。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .writer import MCPKWriter
from .reader import MCPKReader, MCPKError
from .constants import EntryType


def cmd_pack(args):
    """打包文件/目录为 .mcpk。"""
    output = Path(args.output)
    if not output.suffix:
        output = output.with_suffix(".mcpk")

    sources = [Path(p) for p in args.sources]
    for s in sources:
        if not s.exists():
            print(f"错误: 路径不存在: {s}", file=sys.stderr)
            sys.exit(1)

    start = time.time()
    with MCPKWriter(output) as writer:
        for source in sources:
            if source.is_file():
                entry = writer.add_file(source)
                print(f"  + {entry.name} ({_fmt_size(entry.original_size)})")
            elif source.is_dir():
                prefix = args.prefix or ""
                entries = writer.add_directory(source, prefix=prefix)
                for e in entries:
                    print(f"  + {e.name} ({_fmt_size(e.original_size)})")
            else:
                print(f"跳过: {source}", file=sys.stderr)

    elapsed = time.time() - start
    file_size = output.stat().st_size
    print(f"\n打包完成: {output}")
    print(f"  条目数: {writer.entry_count if hasattr(writer, '_entries') else '?'}")
    print(f"  文件大小: {_fmt_size(file_size)}")
    print(f"  耗时: {elapsed:.2f}s")


def cmd_list(args):
    """列出 .mcpk 文件中的条目。"""
    try:
        with MCPKReader(args.file) as reader:
            entries = reader.entries
            if args.type:
                type_map = {"doc": EntryType.DOCUMENT, "image": EntryType.IMAGE, "audio": EntryType.AUDIO}
                filter_type = type_map.get(args.type.lower())
                if filter_type:
                    entries = [e for e in entries if e.entry_type == filter_type]

            if args.json:
                info = reader.inspect()
                print(json.dumps(info, ensure_ascii=False, indent=2))
            else:
                print(f"文件: {reader.file_path}")
                print(f"版本: v{reader.header.version}")
                print(f"条目数: {len(entries)}")
                print()
                print(f"{'类型':<6} {'大小':>10} {'压缩后':>10} {'压缩':>6} {'MIME':<30} {'名称'}")
                print("-" * 90)
                for e in entries:
                    type_name = {0x01: "DOC", 0x02: "IMG", 0x03: "AUD"}.get(e.entry_type, "???")
                    comp_name = {0x00: "-", 0x01: "zlib", 0x02: "zstd", 0x03: "lz4"}.get(e.compression, "?")
                    print(
                        f"{type_name:<6} {_fmt_size(e.original_size):>10} "
                        f"{_fmt_size(e.stored_size):>10} {comp_name:>6} "
                        f"{e.mime_type:<30} {e.name}"
                    )
    except MCPKError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_extract(args):
    """从 .mcpk 提取文件。"""
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        with MCPKReader(args.file) as reader:
            if args.name:
                # 提取单个文件
                try:
                    path = reader.extract_to(args.name, output_dir)
                    print(f"已提取: {path}")
                except KeyError:
                    print(f"错误: 文件不存在: {args.name}", file=sys.stderr)
                    sys.exit(1)
            else:
                # 提取全部
                paths = reader.extract_all(output_dir)
                for p in paths:
                    print(f"  {p}")
                print(f"\n已提取 {len(paths)} 个文件到 {output_dir}")
    except MCPKError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_inspect(args):
    """检查 .mcpk 文件的详细信息。"""
    try:
        with MCPKReader(args.file) as reader:
            info = reader.inspect()
            print(json.dumps(info, ensure_ascii=False, indent=2))
    except MCPKError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_verify(args):
    """验证 .mcpk 文件的完整性。"""
    try:
        with MCPKReader(args.file) as reader:
            errors = reader.verify()
            if errors:
                print(f"验证失败，发现 {len(errors)} 个错误:")
                for err in errors:
                    print(f"  - {err}")
                sys.exit(1)
            else:
                print(f"验证通过: {reader.file_path}")
                print(f"  条目数: {reader.entry_count}")
                print(f"  所有 CRC32 校验正确")
    except MCPKError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


def _fmt_size(n: int) -> str:
    """格式化文件大小。"""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def main():
    parser = argparse.ArgumentParser(
        prog="mcpk",
        description="MCPK (MeCapsule Package) 读写工具",
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # pack
    p_pack = subparsers.add_parser("pack", help="打包文件/目录为 .mcpk")
    p_pack.add_argument("sources", nargs="+", help="源文件或目录")
    p_pack.add_argument("-o", "--output", required=True, help="输出 .mcpk 文件路径")
    p_pack.add_argument("--prefix", help="包内路径前缀")
    p_pack.set_defaults(func=cmd_pack)

    # list
    p_list = subparsers.add_parser("list", help="列出 .mcpk 中的条目")
    p_list.add_argument("file", help=".mcpk 文件路径")
    p_list.add_argument("--type", choices=["doc", "image", "audio"], help="按类型过滤")
    p_list.add_argument("--json", action="store_true", help="输出 JSON 格式")
    p_list.set_defaults(func=cmd_list)

    # extract
    p_extract = subparsers.add_parser("extract", help="提取 .mcpk 中的文件")
    p_extract.add_argument("file", help=".mcpk 文件路径")
    p_extract.add_argument("-o", "--output-dir", default=".", help="输出目录")
    p_extract.add_argument("-n", "--name", help="提取指定文件名（不指定则提取全部）")
    p_extract.set_defaults(func=cmd_extract)

    # inspect
    p_inspect = subparsers.add_parser("inspect", help="检查 .mcpk 文件详情")
    p_inspect.add_argument("file", help=".mcpk 文件路径")
    p_inspect.set_defaults(func=cmd_inspect)

    # verify
    p_verify = subparsers.add_parser("verify", help="验证 .mcpk 文件完整性")
    p_verify.add_argument("file", help=".mcpk 文件路径")
    p_verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
