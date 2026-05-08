"""MCPK 格式集成测试。"""

import json
import os
import sys
import tempfile
from pathlib import Path

# 确保能导入 mcpk 包
sys.path.insert(0, str(Path(__file__).parent))

from mcpk.writer import MCPKWriter
from mcpk.reader import MCPKReader, MCPKError
from mcpk.constants import EntryType, Compression


def create_test_files(base_dir: Path) -> dict[str, Path]:
    """创建测试文件并返回路径映射。"""
    files = {}

    # 文档
    doc_dir = base_dir / "documents"
    doc_dir.mkdir()

    md_file = doc_dir / "readme.md"
    md_content = """# 测试文档

这是一个 MCPK 格式的测试文档。

## 功能

- 支持文档、图片、音频打包
- 每条目独立压缩
- CRC32 完整性校验
- JSON 元数据

## 作者

MeCapsule Team
"""
    md_file.write_text(md_content, encoding="utf-8")
    files["readme.md"] = md_file

    txt_file = doc_dir / "notes.txt"
    txt_content = "这是一些简单的笔记内容。\n第二行。\n第三行。\n" * 10
    txt_file.write_text(txt_content, encoding="utf-8")
    files["notes.txt"] = txt_file

    json_file = doc_dir / "config.json"
    json_data = {
        "app": "MeCapsule",
        "version": "1.0.0",
        "settings": {"theme": "dark", "language": "zh-CN"}
    }
    json_file.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")
    files["config.json"] = json_file

    # 图片 (创建一个简单的 BMP 文件)
    img_dir = base_dir / "images"
    img_dir.mkdir()

    # 简单的 2x2 红色 BMP (未压缩, 适合测试压缩)
    bmp_file = img_dir / "test.bmp"
    bmp_data = _create_simple_bmp()
    bmp_file.write_bytes(bmp_data)
    files["test.bmp"] = bmp_file

    # 模拟一个 JPEG (已经是压缩格式，不会被再次压缩)
    jpg_file = img_dir / "photo.jpg"
    # 用一些伪随机数据模拟 JPEG
    jpg_data = bytes(range(256)) * 40  # 10KB
    jpg_file.write_bytes(jpg_data)
    files["photo.jpg"] = jpg_file

    # 音频 (创建一个简单的 WAV)
    aud_dir = base_dir / "audio"
    aud_dir.mkdir()

    wav_file = aud_dir / "tone.wav"
    wav_data = _create_simple_wav()
    wav_file.write_bytes(wav_data)
    files["tone.wav"] = wav_file

    return files


def _create_simple_bmp() -> bytes:
    """创建一个最小的 2x2 像素红色 BMP 文件。"""
    import struct
    width, height = 2, 2
    row_size = (width * 3 + 3) & ~3  # 每行对齐到 4 字节
    pixel_data_size = row_size * height
    file_size = 54 + pixel_data_size

    # BMP 文件头
    header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, 54)
    # DIB 头 (BITMAPINFOHEADER)
    dib = struct.pack("<IiiHHIIiiII",
        40,           # 头大小
        width,        # 宽度
        height,       # 高度
        1,            # 颜色平面数
        24,           # 位深度
        0,            # 压缩方式
        pixel_data_size,  # 图像大小
        2835,         # 水平分辨率
        2835,         # 垂直分辨率
        0,            # 调色板颜色数
        0,            # 重要颜色数
    )

    # 像素数据 (红色 BGR)
    pixels = b""
    for y in range(height):
        row = b""
        for x in range(width):
            row += b"\x00\x00\xFF"  # 红色 BGR
        row += b"\x00" * (row_size - width * 3)  # 对齐填充
        pixels += row

    return header + dib + pixels


def _create_simple_wav() -> bytes:
    """创建一个最简单的 WAV 文件 (1秒 440Hz 正弦波)。"""
    import struct
    import math

    sample_rate = 44100
    duration = 0.5  # 0.5 秒
    num_samples = int(sample_rate * duration)

    # 生成正弦波样本 (16-bit)
    samples = []
    for i in range(num_samples):
        t = i / sample_rate
        value = int(32767 * math.sin(2 * math.pi * 440 * t))
        samples.append(struct.pack("<h", value))

    data = b"".join(samples)
    data_size = len(data)

    # WAV 头
    header = struct.pack("<4sI4s", b"RIFF", 36 + data_size, b"WAVE")
    fmt = struct.pack("<4sIHHIIHH",
        b"fmt ", 16, 1, 1, sample_rate,
        sample_rate * 2, 2, 16)
    data_header = struct.pack("<4sI", b"data", data_size)

    return header + fmt + data_header + data


def test_roundtrip():
    """测试完整的写入 → 读取 → 验证流程。"""
    print("=" * 60)
    print("MCPK 集成测试")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # 1. 创建测试文件
        print("\n[1/5] 创建测试文件...")
        files = create_test_files(base)
        for name, path in files.items():
            print(f"  {name}: {path.stat().st_size} bytes")

        # 2. 打包
        print("\n[2/5] 打包为 .mcpk...")
        mcpk_path = base / "test.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            for name, path in files.items():
                metadata = {"title": f"测试-{name}", "tags": ["test", "demo"]}
                entry = writer.add_file(path, metadata=metadata)
                print(f"  + {entry.name}: {entry.original_size} -> {entry.stored_size} "
                      f"({entry.compression.name})")

        file_size = mcpk_path.stat().st_size
        print(f"\n  输出文件: {file_size} bytes")

        # 3. 读取和验证
        print("\n[3/5] 读取并验证...")
        with MCPKReader(mcpk_path) as reader:
            # 验证完整性
            errors = reader.verify()
            if errors:
                print(f"  验证失败!")
                for err in errors:
                    print(f"    - {err}")
                return False
            print("  完整性验证通过 ✓")

            # 列出条目
            print(f"\n  条目列表 ({reader.entry_count} 个):")
            for e in reader.entries:
                meta = e.metadata_dict()
                title = meta.get("title", "-")
                print(f"    [{EntryType(e.entry_type).name}] {e.name} "
                      f"({e.original_size}B, {Compression(e.compression).name}) "
                      f"title={title}")

        # 4. 提取并比对
        print("\n[4/5] 提取并比对内容...")
        with MCPKReader(mcpk_path) as reader:
            for name, original_path in files.items():
                extracted_data = reader.extract(name)
                original_data = original_path.read_bytes()

                if extracted_data == original_data:
                    print(f"  {name}: 内容一致 ✓")
                else:
                    print(f"  {name}: 内容不一致 ✗")
                    return False

        # 5. 提取到目录
        print("\n[5/5] 提取到目录...")
        extract_dir = base / "extracted"
        with MCPKReader(mcpk_path) as reader:
            paths = reader.extract_all(extract_dir)
            for p in paths:
                rel = p.relative_to(extract_dir)
                print(f"  {rel}")

            # 验证提取的文件与原始文件一致
            for p in paths:
                rel = str(p.relative_to(extract_dir))
                # 查找对应的原始文件
                for name, orig_path in files.items():
                    if name == Path(rel).name:
                        if p.read_bytes() == orig_path.read_bytes():
                            print(f"  {rel}: 与原始文件一致 ✓")
                        else:
                            print(f"  {rel}: 与原始文件不一致 ✗")
                            return False
                        break

    print("\n" + "=" * 60)
    print("全部测试通过 ✓")
    print("=" * 60)
    return True


def test_inspect():
    """测试 inspect 功能。"""
    print("\n\n[附加] 测试 inspect 输出:")
    print("-" * 40)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        files = create_test_files(base)

        mcpk_path = base / "inspect_test.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            for name, path in files.items():
                writer.add_file(path)

        with MCPKReader(mcpk_path) as reader:
            info = reader.inspect()
            print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    success = test_roundtrip()
    test_inspect()
    sys.exit(0 if success else 1)
