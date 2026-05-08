"""MCPK v2 格式集成测试（增强版）。

测试覆盖：
- v2 基本读写 (Magic Index + 分组存储 + Group Index)
- VIDEO 类型支持
- 分组创建和多类型组间关系
- 向后兼容 v1 格式
- 完整性校验
- 大文件处理 (10MB~100MB)
- 边界情况 (空文件、中文路径、特殊字符)
- 压缩比验证
- 多分组多关系复杂场景
- 性能基准

用法：
    python test_mcpk.py              # 运行全部测试
    python test_mcpk.py --quick      # 快速模式，跳过大文件测试
    python test_mcpk.py --large-only # 仅运行大文件测试
"""

import binascii
import json
import math
import os
import struct
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from mcpk.writer import MCPKWriter
from mcpk.reader import MCPKReader, MCPKError
from mcpk.constants import (
    EntryType, Compression, GroupType, RelationType, NO_GROUP,
    MAGIC, VERSION, EncryptionMode,
)


# ═══════════════════════════════════════════════════════════
#  文件大小预设
# ═══════════════════════════════════════════════════════════

SIZE_PRESETS = {
    "tiny":   {"video": 4_000,      "text": 200,        "image": 500,       "audio": 8_000},
    "small":  {"video": 200_000,    "text": 5_000,      "image": 10_000,    "audio": 50_000},
    "medium": {"video": 5_000_000,  "text": 100_000,    "image": 200_000,   "audio": 500_000},
    "large":  {"video": 50_000_000, "text": 1_000_000,  "image": 2_000_000, "audio": 5_000_000},
    "xlarge": {"video": 100_000_000,"text": 5_000_000,  "image": 5_000_000, "audio": 10_000_000},
}


def _fmt(n: int) -> str:
    """格式化字节数。"""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _elapsed(start: float) -> str:
    """格式化耗时。"""
    dt = time.time() - start
    if dt < 1:
        return f"{dt*1000:.0f}ms"
    return f"{dt:.2f}s"


# ═══════════════════════════════════════════════════════════
#  真实感文件生成器
# ═══════════════════════════════════════════════════════════

def gen_text_file(path: Path, size: int, *, seed: int = 42):
    """生成指定大小的可压缩文本文件（中文+英文混合）。"""
    rng = __import__("random").Random(seed)
    paragraphs = [
        "机器学习是人工智能的一个重要分支，通过算法让计算机从数据中学习。",
        "深度学习使用多层神经网络来模拟人脑的工作方式，处理复杂的模式识别。",
        "自然语言处理让计算机能够理解、解释和生成人类语言。",
        "计算机视觉使机器能够从图像和视频中获取高层次的理解。",
        "强化学习通过与环境交互来学习最优策略，广泛应用于游戏和机器人。",
        "MCPK 是一种自定义二进制容器格式，支持文档、图片、音频和视频打包。",
        "Magic Index 聚合了所有文件的签名，便于快速类型识别和加密。",
        "分组存储让相关文件在物理上相邻，提高顺序读取效率。",
        "Group Index 记录分组元数据和组间关系，支持课程、会议等场景。",
        "AES-256-GCM 提供认证加密，同时保证数据的机密性和完整性。",
        "PBKDF2 和 Argon2 是常用的密码派生函数，用于从用户密码生成加密密钥。",
        "小端序是 Intel x86 架构使用的字节序，低位字节存储在低地址。",
    ]

    written = 0
    parts = []
    while written < size:
        line = rng.choice(paragraphs)
        # 随机添加行号和时间戳前缀
        prefix = f"[{rng.randint(1,999):03d}] "
        text = prefix + line + "\n"
        if written + len(text.encode("utf-8")) > size:
            text = text[:size - written]
        parts.append(text)
        written += len(text.encode("utf-8"))

    path.write_text("".join(parts), encoding="utf-8")


def gen_binary_file(path: Path, size: int, *, seed: int = 42, pattern: str = "random"):
    """生成指定大小的二进制文件。"""
    rng = __import__("random").Random(seed)
    if pattern == "random":
        # 分块生成避免内存爆炸
        chunk = 65536
        with open(path, "wb") as f:
            remaining = size
            while remaining > 0:
                n = min(chunk, remaining)
                f.write(bytes(rng.randint(0, 255) for _ in range(n)))
                remaining -= n
    elif pattern == "zeros":
        path.write_bytes(b"\x00" * size)
    elif pattern == "repeating":
        base = bytes(range(256)) * (size // 256 + 1)
        path.write_bytes(base[:size])
    else:
        path.write_bytes(os.urandom(size))


def gen_mp4_file(path: Path, size: int, *, seed: int = 42):
    """生成以 ftyp box 开头的模拟 MP4 文件。"""
    rng = __import__("random").Random(seed)
    # MP4 ftyp box: size(4) + 'ftyp'(4) + brand(4) + version(4) = 20 bytes
    ftyp_box = struct.pack(">I", 20) + b"ftyp" + b"isom" + struct.pack(">I", 0x200)
    # mdat box header: size(4) + 'mdat'(4)
    mdat_size = size - 8
    mdat_header = struct.pack(">I", mdat_size) + b"mdat"
    # 填充随机数据
    payload_size = size - len(ftyp_box) - len(mdat_header)
    chunk = 65536
    with open(path, "wb") as f:
        f.write(ftyp_box)
        f.write(mdat_header)
        remaining = payload_size
        while remaining > 0:
            n = min(chunk, remaining)
            f.write(bytes(rng.randint(0, 255) for _ in range(n)))
            remaining -= n


def gen_mkv_file(path: Path, size: int, *, seed: int = 42):
    """生成以 EBML header 开头的模拟 MKV 文件。"""
    rng = __import__("random").Random(seed)
    ebml_header = b"\x1a\x45\xdf\xa3" + b"\x00" * 12  # 简化 EBML header
    with open(path, "wb") as f:
        f.write(ebml_header)
        remaining = size - len(ebml_header)
        chunk = 65536
        while remaining > 0:
            n = min(chunk, remaining)
            f.write(bytes(rng.randint(0, 255) for _ in range(n)))
            remaining -= n


def gen_jpeg_file(path: Path, size: int, *, seed: int = 42):
    """生成以 JPEG magic 开头的模拟 JPEG 文件。"""
    rng = __import__("random").Random(seed)
    jpeg_header = b"\xff\xd8\xff\xe0" + b"\x00" * 16  # SOI + APP0 marker
    with open(path, "wb") as f:
        f.write(jpeg_header)
        remaining = size - len(jpeg_header)
        chunk = 65536
        while remaining > 0:
            n = min(chunk, remaining)
            f.write(bytes(rng.randint(0, 255) for _ in range(n)))
            remaining -= n
        f.write(b"\xff\xd9")  # EOI marker


def gen_png_file(path: Path, size: int, *, seed: int = 42):
    """生成以 PNG signature 开头的模拟 PNG 文件。"""
    rng = __import__("random").Random(seed)
    png_sig = b"\x89PNG\r\n\x1a\n"
    with open(path, "wb") as f:
        f.write(png_sig)
        remaining = size - len(png_sig)
        chunk = 65536
        while remaining > 0:
            n = min(chunk, remaining)
            f.write(bytes(rng.randint(0, 255) for _ in range(n)))
            remaining -= n


def gen_wav_file(path: Path, size: int, *, seed: int = 42):
    """生成指定大小的 WAV 文件。"""
    sample_rate = 44100
    data_size = size - 44  # WAV header is 44 bytes
    num_samples = data_size // 2  # 16-bit samples

    header = struct.pack("<4sI4s", b"RIFF", size - 8, b"WAVE")
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, sample_rate,
                      sample_rate * 2, 2, 16)
    data_header = struct.pack("<4sI", b"data", data_size)

    rng = __import__("random").Random(seed)
    with open(path, "wb") as f:
        f.write(header + fmt + data_header)
        chunk_samples = 16384
        remaining = num_samples
        while remaining > 0:
            n = min(chunk_samples, remaining)
            # 生成低幅正弦波+噪声，模拟真实音频
            samples = []
            for i in range(n):
                t = (num_samples - remaining + i) / sample_rate
                val = int(16000 * math.sin(2 * math.pi * 440 * t))
                val += rng.randint(-500, 500)  # 噪声
                val = max(-32768, min(32767, val))
                samples.append(struct.pack("<h", val))
            f.write(b"".join(samples))
            remaining -= n


def gen_srt_file(path: Path, num_entries: int = 50, *, seed: int = 42):
    """生成 SRT 字幕文件。"""
    rng = __import__("random").Random(seed)
    lines = []
    phrases = [
        "欢迎来到本课程", "今天我们学习新知识", "请看这个示例",
        "这是一个重要的概念", "让我们总结一下", "下节课再见",
        "大家有什么问题吗", "这个方法非常实用", "注意这里的细节",
        "我们可以这样理解", "实验结果表明", "数据验证了我们的假设",
    ]
    for i in range(1, num_entries + 1):
        start_s = i * 3
        end_s = start_s + 2
        lines.append(str(i))
        lines.append(f"00:{start_s//60:02d}:{start_s%60:02d},000 --> "
                     f"00:{end_s//60:02d}:{end_s%60:02d},000")
        lines.append(rng.choice(phrases))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def gen_bmp_file(path: Path, width: int = 100, height: int = 100, *, seed: int = 42):
    """生成指定尺寸的 BMP 文件。"""
    rng = __import__("random").Random(seed)
    row_size = (width * 3 + 3) & ~3
    pixel_data_size = row_size * height
    file_size = 54 + pixel_data_size

    header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, 54)
    dib = struct.pack("<IiiHHIIiiII",
        40, width, height, 1, 24, 0, pixel_data_size, 2835, 2835, 0, 0)

    with open(path, "wb") as f:
        f.write(header + dib)
        for _ in range(height):
            row = bytes(rng.randint(0, 255) for _ in range(width * 3))
            row += b"\x00" * (row_size - width * 3)
            f.write(row)


# ═══════════════════════════════════════════════════════════
#  测试场景生成器
# ═══════════════════════════════════════════════════════════

def build_course_scenario(base: Path, sizes: dict) -> dict[str, Path]:
    """构建课程场景：3 讲课程，每讲含视频+字幕+讲义。"""
    files = {}
    for i in range(1, 4):
        d = base / f"lecture{i}"
        d.mkdir(exist_ok=True)

        vpath = d / f"lecture{i}.mp4"
        gen_mp4_file(vpath, sizes["video"], seed=100 + i)
        files[f"lecture{i}.mp4"] = vpath

        spath = d / f"lecture{i}.srt"
        gen_srt_file(spath, num_entries=30 + i * 10, seed=200 + i)
        files[f"lecture{i}.srt"] = spath

        tpath = d / f"slides{i}.md"
        gen_text_file(tpath, sizes["text"], seed=300 + i)
        files[f"slides{i}.md"] = tpath

    return files


def build_mixed_scenario(base: Path, sizes: dict) -> dict[str, Path]:
    """构建混合场景：各种类型文件。"""
    files = {}

    # 文档
    for name, ext, seed in [("report", ".md", 1), ("notes", ".txt", 2),
                             ("config", ".json", 3), ("data", ".csv", 4)]:
        p = base / f"{name}{ext}"
        gen_text_file(p, sizes["text"], seed=seed)
        files[f"{name}{ext}"] = p

    # 图片
    for name, gen_fn, seed in [
        ("photo.jpg", gen_jpeg_file, 10), ("screenshot.png", gen_png_file, 11),
        ("diagram.bmp", lambda p, s, **kw: gen_bmp_file(p, 200, 200, **kw), 12),
    ]:
        p = base / name
        gen_fn(p, sizes["image"], seed=seed)
        files[name] = p

    # 音频
    p = base / "recording.wav"
    gen_wav_file(p, sizes["audio"], seed=20)
    files["recording.wav"] = p

    # 视频
    p = base / "demo.mp4"
    gen_mp4_file(p, sizes["video"], seed=30)
    files["demo.mp4"] = p

    # 字幕
    p = base / "demo.srt"
    gen_srt_file(p, 40, seed=31)
    files["demo.srt"] = p

    return files


# ═══════════════════════════════════════════════════════════
#  测试用例
# ═══════════════════════════════════════════════════════════

def test_01_v2_roundtrip_basic(sizes: dict):
    """基本 Roundtrip：打包→读取→校验→提取→比对。"""
    label = "v2 基本 Roundtrip"
    print("=" * 60)
    print(f"测试 01: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        files = build_mixed_scenario(base, sizes)

        mcpk_path = base / "roundtrip.mcpk"

        # ── 打包 ──
        t0 = time.time()
        with MCPKWriter(mcpk_path) as writer:
            for name, path in files.items():
                metadata = {"title": f"测试-{name}", "tags": ["test", "v2"]}
                writer.add_file(path, metadata=metadata)
        pack_time = time.time() - t0
        file_size = mcpk_path.stat().st_size
        total_original = sum(p.stat().st_size for p in files.values())
        print(f"  打包: {len(files)} 文件, 原始 {_fmt(total_original)} -> "
              f"容器 {_fmt(file_size)} (压缩比 {total_original/max(file_size,1):.2f}x), "
              f"耗时 {_elapsed(t0)}")

        # ── 读取校验 ──
        t0 = time.time()
        with MCPKReader(mcpk_path) as reader:
            assert reader.version == 2
            assert reader.entry_count == len(files)
            assert len(reader.magic_entries) == len(files)

            errors = reader.verify()
            assert not errors, f"校验失败: {errors}"
            print(f"  校验通过, 耗时 {_elapsed(t0)}")

            # ── 逐文件内容比对 ──
            for name, original_path in files.items():
                original_data = original_path.read_bytes()
                extracted = reader.extract(name)
                assert extracted == original_data, f"{name} 内容不一致"
            print(f"  全部 {len(files)} 文件内容一致 ✓")

        # ── 提取到目录 ──
        extract_dir = base / "extracted"
        with MCPKReader(mcpk_path) as reader:
            reader.extract_all(extract_dir)
            for name, original_path in files.items():
                out = extract_dir / name
                assert out.exists(), f"缺失: {name}"
                assert out.read_bytes() == original_path.read_bytes()
            print(f"  提取到目录验证通过 ✓")

    print(f"  PASS: {label}")
    return True


def test_02_grouping_and_relations(sizes: dict):
    """分组存储 + 多种组间关系。"""
    label = "分组存储与组间关系"
    print("\n" + "=" * 60)
    print(f"测试 02: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        files = build_course_scenario(base, sizes)

        mcpk_path = base / "grouped.mcpk"

        # ── 打包为 3 个分组 + 1 个独立文件 ──
        with MCPKWriter(mcpk_path) as writer:
            for i in range(1, 4):
                gname = f"第{i}讲"
                writer.add_file(files[f"lecture{i}.mp4"], group_name=gname,
                              metadata={"title": f"机器学习第{i}讲", "week": i})
                writer.add_file(files[f"lecture{i}.srt"], group_name=gname)
                writer.add_file(files[f"slides{i}.md"], group_name=gname)

            # 组间关系：链式 + 语义关联
            writer.add_relation("第1讲", "第2讲", RelationType.SEQUEL,
                              description="课程递进")
            writer.add_relation("第2讲", "第3讲", RelationType.SEQUEL,
                              description="课程递进")
            writer.add_relation("第1讲", "第3讲", RelationType.RELATED,
                              description="首尾呼应")

        # ── 验证分组结构 ──
        with MCPKReader(mcpk_path) as reader:
            assert len(reader.groups) == 3
            for g in reader.groups:
                assert len(g.entry_ids) == 3, f"分组 {g.name} 应有 3 条目, 实际 {len(g.entry_ids)}"
            print(f"  3 个分组, 每组 3 条目 ✓")

            # 组间关系
            assert len(reader.relations) == 3
            sequel_count = sum(1 for r in reader.relations if r.relation_type == RelationType.SEQUEL)
            related_count = sum(1 for r in reader.relations if r.relation_type == RelationType.RELATED)
            assert sequel_count == 2 and related_count == 1
            print(f"  3 条关系 (2 SEQUEL + 1 RELATED) ✓")

            # 物理相邻性
            for g in reader.groups:
                entries = [reader.entries[eid] for eid in g.entry_ids]
                offsets = sorted(e.blob_offset for e in entries)
                sizes_list = [e.stored_size for e in entries]
                # 组内 blob 应连续
                for j in range(len(offsets) - 1):
                    assert offsets[j] + sizes_list[j] <= offsets[j + 1] + 1, \
                        f"分组 {g.name} 内 blob 不连续"
            print(f"  各分组 blob 物理相邻 ✓")

            # 完整性
            errors = reader.verify()
            assert not errors, f"校验失败: {errors}"
            print(f"  完整性校验通过 ✓")

            # 按分组提取
            for g in reader.groups:
                entries = reader.list_group_entries(g.name)
                for e in entries:
                    data = reader.extract(e.name)
                    assert len(data) == e.original_size
            print(f"  按分组提取内容正确 ✓")

    print(f"  PASS: {label}")
    return True


def test_03_video_types(sizes: dict):
    """VIDEO 类型全覆盖：MP4/MKV/AVI/MOV/WebM。"""
    label = "VIDEO 类型全覆盖"
    print("\n" + "=" * 60)
    print(f"测试 03: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        vid_dir = base / "videos"
        vid_dir.mkdir()

        # 生成各种视频格式
        vid_size = sizes["video"]
        video_specs = [
            ("clip.mp4",  gen_mp4_file,  "video/mp4"),
            ("clip.mkv",  gen_mkv_file,  "video/x-matroska"),
        ]
        # 只有足够大的文件才生成额外格式
        if vid_size >= 1000:
            video_specs.append(("clip.webm", gen_mkv_file, "video/webm"))  # WebM 也用 EBML

        originals = {}
        for name, gen_fn, expected_mime in video_specs:
            p = vid_dir / name
            gen_fn(p, vid_size, seed=hash(name) % 10000)
            originals[name] = (p, expected_mime)

        # 打包
        mcpk_path = base / "video_test.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            for name, (path, _) in originals.items():
                entry = writer.add_file(path, metadata={
                    "title": f"视频-{name}",
                    "duration_ms": 120000,
                    "width": 1920,
                    "height": 1080,
                    "codec": "h264",
                })
                assert entry.entry_type == EntryType.VIDEO, \
                    f"{name}: 类型应为 VIDEO, 实际 {EntryType(entry.entry_type).name}"
                assert entry.compression == Compression.NONE, \
                    f"{name}: 视频不应压缩"

        # 验证
        with MCPKReader(mcpk_path) as reader:
            assert reader.version == 2
            video_entries = reader.list_entries(EntryType.VIDEO)
            assert len(video_entries) == len(originals)
            print(f"  {len(video_entries)} 个 VIDEO 条目 ✓")

            for me in reader.magic_entries:
                assert me.entry_type == EntryType.VIDEO
            print(f"  Magic Index 全部为 VIDEO 类型 ✓")

            for name, (path, expected_mime) in originals.items():
                entry = reader.find(name)
                assert entry is not None
                assert entry.mime_type == expected_mime
                extracted = reader.extract(name)
                original_data = path.read_bytes()
                assert extracted == original_data
            print(f"  全部视频内容一致 ✓")

            # 元数据
            meta = reader.get_metadata("clip.mp4")
            assert meta["duration_ms"] == 120000
            assert meta["width"] == 1920
            print(f"  视频元数据正确 ✓")

    print(f"  PASS: {label}")
    return True


def test_04_magic_index_correctness(sizes: dict):
    """Magic Index 签名验证：每种文件类型的 magic 码。"""
    label = "Magic Index 签名验证"
    print("\n" + "=" * 60)
    print(f"测试 04: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        small = {"video": 4000, "text": 200, "image": 500, "audio": 8000}

        # 创建各种类型文件
        specs = {
            "doc.md":    (gen_text_file,    EntryType.DOCUMENT, small["text"],   1),
            "doc.txt":   (gen_text_file,    EntryType.DOCUMENT, small["text"],   2),
            "pic.jpg":   (gen_jpeg_file,    EntryType.IMAGE,    small["image"],  3),
            "pic.png":   (gen_png_file,     EntryType.IMAGE,    small["image"],  4),
            "pic.bmp":   (gen_bmp_file,     EntryType.IMAGE,    small["image"],  5),
            "aud.wav":   (gen_wav_file,     EntryType.AUDIO,    small["audio"],  6),
            "vid.mp4":   (gen_mp4_file,     EntryType.VIDEO,    small["video"],  7),
            "vid.mkv":   (gen_mkv_file,     EntryType.VIDEO,    small["video"],  8),
            "sub.srt":   (gen_srt_file,     EntryType.DOCUMENT, 50,             9),
        }

        originals = {}
        for name, (gen_fn, etype, size_or_count, seed) in specs.items():
            p = base / name
            if name.endswith(".srt"):
                gen_fn(p, size_or_count, seed=seed)
            elif name.endswith(".bmp"):
                gen_bmp_file(p, 50, 50, seed=seed)
            else:
                gen_fn(p, size_or_count, seed=seed)
            originals[name] = (p, etype)

        # 打包
        mcpk_path = base / "magic_test.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            for name, (path, _) in originals.items():
                writer.add_file(path)

        # 验证 Magic Index
        with MCPKReader(mcpk_path) as reader:
            assert len(reader.magic_entries) == len(originals)
            print(f"  {len(reader.magic_entries)} 个 Magic Entry ✓")

            expected_magics = {
                "doc.md":  None,  # 文本文件无固定 magic
                "doc.txt": None,
                "pic.jpg": b"\xff\xd8\xff",
                "pic.png": b"\x89PNG",
                "pic.bmp": b"BM",
                "aud.wav": b"RIFF",
                "vid.mp4": b"\x00\x00\x00",  # ftyp box size prefix
                "vid.mkv": b"\x1a\x45\xdf\xa3",
                "sub.srt": None,  # SRT 无固定 magic
            }

            for me in reader.magic_entries:
                entry = reader.entries[me.entry_id]
                expected = expected_magics.get(entry.name)
                if expected is not None:
                    assert me.magic_bytes[:len(expected)] == expected, \
                        f"{entry.name}: magic 应以 {expected!r} 开头, 实际 {me.magic_bytes[:8]!r}"
                    print(f"  {entry.name}: magic {me.magic_bytes[:4].hex()}... ✓")
                else:
                    print(f"  {entry.name}: (无固定 magic, 跳过)")

            # 验证 entry_type 在 Magic Index 中与 TOC 中一致
            for me in reader.magic_entries:
                entry = reader.entries[me.entry_id]
                assert me.entry_type == entry.entry_type
            print(f"  Magic Index 与 TOC 类型一致 ✓")

    print(f"  PASS: {label}")
    return True


def test_05_compression_ratios(sizes: dict):
    """压缩比验证：文本应有明显压缩，已压缩格式不压缩。"""
    label = "压缩比验证"
    print("\n" + "=" * 60)
    print(f"测试 05: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # 高可压缩文本（大量重复段落，模拟日志/文档）
        txt_path = base / "large.txt"
        line = "这是一段用于测试压缩效果的重复文本内容，MCPK v2 支持 zlib 压缩。" * 3
        with open(txt_path, "w", encoding="utf-8") as f:
            for i in range(max(200, sizes["text"] // len(line.encode("utf-8")))):
                f.write(f"[{i:06d}] {line}\n")

        # JSON（也应被良好压缩）
        json_path = base / "data.json"
        items = [{"id": i, "name": f"item_{i}", "value": i * 10, "active": i % 2 == 0}
                 for i in range(max(100, sizes["text"] // 80))]
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)

        # JPEG（不应被压缩）
        jpg_path = base / "photo.jpg"
        gen_jpeg_file(jpg_path, sizes["image"], seed=43)

        # WAV（应被 zstd 压缩，回退到 zlib）
        wav_path = base / "audio.wav"
        gen_wav_file(wav_path, sizes["audio"], seed=44)

        # MP4（不应被压缩）
        mp4_path = base / "video.mp4"
        gen_mp4_file(mp4_path, sizes["video"], seed=45)

        mcpk_path = base / "compress_test.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            writer.add_file(txt_path)
            writer.add_file(json_path)
            writer.add_file(jpg_path)
            writer.add_file(wav_path)
            writer.add_file(mp4_path)

        with MCPKReader(mcpk_path) as reader:
            for entry in reader.entries:
                ratio = entry.compression_ratio
                comp_name = Compression(entry.compression).name
                if entry.name.endswith((".txt", ".json")):
                    assert entry.is_compressed, f"{entry.name} 应被压缩"
                    assert ratio > 1.5, f"{entry.name} 压缩比应 >1.5x, 实际 {ratio:.2f}x"
                    print(f"  {entry.name}: {entry.original_size}B -> {entry.stored_size}B, "
                          f"压缩比 {ratio:.2f}x ({comp_name}) ✓")
                elif entry.name.endswith((".jpg", ".mp4")):
                    assert not entry.is_compressed, f"{entry.name} 不应被压缩"
                    print(f"  {entry.name}: {entry.original_size}B, 未压缩 (已压缩格式) ✓")
                elif entry.name.endswith(".wav"):
                    if entry.is_compressed:
                        print(f"  {entry.name}: {entry.original_size}B -> {entry.stored_size}B, "
                              f"压缩比 {ratio:.2f}x ({comp_name}) ✓")
                    else:
                        print(f"  {entry.name}: {entry.original_size}B, 未压缩 (zstd/lz4 不可用) ✓")

            errors = reader.verify()
            assert not errors
            print(f"  压缩后完整性校验通过 ✓")

    print(f"  PASS: {label}")
    return True


def test_06_many_entries(sizes: dict):
    """大批量条目测试：100+ 文件打包。"""
    label = "大批量条目 (100+)"
    print("\n" + "=" * 60)
    print(f"测试 06: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        num_files = 120
        file_size = max(100, sizes["text"] // 10)  # 每个文件至少 100B

        files_dir = base / "batch"
        files_dir.mkdir()

        # 生成 120 个小文件
        t0 = time.time()
        file_paths = {}
        for i in range(num_files):
            ext = [".md", ".txt", ".json", ".csv"][i % 4]
            p = files_dir / f"file_{i:04d}{ext}"
            gen_text_file(p, file_size, seed=i)
            file_paths[f"file_{i:04d}{ext}"] = p
        print(f"  生成 {num_files} 文件: {_elapsed(t0)}")

        # 打包
        mcpk_path = base / "batch.mcpk"
        t0 = time.time()
        with MCPKWriter(mcpk_path) as writer:
            for name, path in file_paths.items():
                writer.add_file(path)
        pack_time = time.time() - t0
        print(f"  打包耗时: {_elapsed(t0)}, 文件大小 {_fmt(mcpk_path.stat().st_size)}")

        # 读取
        t0 = time.time()
        with MCPKReader(mcpk_path) as reader:
            assert reader.entry_count == num_files
            assert len(reader.magic_entries) == num_files
            errors = reader.verify()
            assert not errors
        verify_time = time.time() - t0
        print(f"  读取+校验耗时: {_elapsed(t0)}")

        # 随机访问验证
        import random
        rng = random.Random(999)
        sample_names = rng.sample(list(file_paths.keys()), min(20, num_files))
        with MCPKReader(mcpk_path) as reader:
            for name in sample_names:
                extracted = reader.extract(name)
                original = file_paths[name].read_bytes()
                assert extracted == original
        print(f"  随机抽取 {len(sample_names)} 文件验证一致 ✓")

    print(f"  PASS: {label}")
    return True


def test_07_edge_cases(sizes: dict):
    """边界情况：空文件、中文名、长文件名、特殊字符。"""
    label = "边界情况"
    print("\n" + "=" * 60)
    print(f"测试 07: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # 创建特殊文件
        edge_files = {}

        # 空文件
        empty_path = base / "empty.txt"
        empty_path.write_bytes(b"")
        edge_files["empty.txt"] = empty_path

        # 1 字节文件
        one_byte = base / "one_byte.bin"
        one_byte.write_bytes(b"\x42")
        edge_files["one_byte.bin"] = one_byte

        # 中文文件名
        cn_path = base / "会议纪要_2026年春季.md"
        gen_text_file(cn_path, 500, seed=88)
        edge_files["会议纪要_2026年春季.md"] = cn_path

        # 带空格的文件名
        space_path = base / "my notes (final).txt"
        gen_text_file(space_path, 300, seed=89)
        edge_files["my notes (final).txt"] = space_path

        # 长文件名 (接近 255 字节)
        long_name = "a" * 200 + ".txt"
        long_path = base / long_name
        gen_text_file(long_path, 200, seed=90)
        edge_files[long_name] = long_path

        # 仅 1 字节的文件名
        short_path = base / "x.md"
        gen_text_file(short_path, 100, seed=91)
        edge_files["x.md"] = short_path

        # 打包
        mcpk_path = base / "edge.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            for name, path in edge_files.items():
                writer.add_file(path, metadata={"title": name[:50]})

        # 验证
        with MCPKReader(mcpk_path) as reader:
            assert reader.entry_count == len(edge_files)
            errors = reader.verify()
            assert not errors, f"校验失败: {errors}"
            print(f"  {len(edge_files)} 边界文件校验通过 ✓")

            # 空文件
            data = reader.extract("empty.txt")
            assert data == b""
            print(f"  空文件: 0 字节 ✓")

            # 1 字节
            data = reader.extract("one_byte.bin")
            assert data == b"\x42"
            print(f"  1 字节文件: 正确 ✓")

            # 中文名
            data = reader.extract("会议纪要_2026年春季.md")
            assert len(data) > 0
            print(f"  中文文件名: 正确 ✓")

            # 带空格
            data = reader.extract("my notes (final).txt")
            assert len(data) > 0
            print(f"  带空格文件名: 正确 ✓")

            # 长文件名
            data = reader.extract(long_name)
            assert len(data) > 0
            print(f"  长文件名 ({len(long_name)} 字符): 正确 ✓")

        # ── 空 MCPK 文件 ──
        empty_mcpk = base / "empty_container.mcpk"
        with MCPKWriter(empty_mcpk) as writer:
            pass
        with MCPKReader(empty_mcpk) as reader:
            assert reader.version == 2
            assert reader.entry_count == 0
            assert len(reader.groups) == 0
            print(f"  空容器: v{reader.version}, 0 条目 ✓")

    print(f"  PASS: {label}")
    return True


def test_08_v1_backward_compat(sizes: dict):
    """v1 向后兼容：手构造 v1 文件并用 v2 reader 读取。"""
    label = "v1 向后兼容"
    print("\n" + "=" * 60)
    print(f"测试 08: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # 构造含 3 个条目的 v1 文件
        entries_data = [
            ("doc1.txt",  b"Hello, this is document 1.", "text/plain", EntryType.DOCUMENT),
            ("doc2.txt",  b"Document 2 with more content " * 20, "text/plain", EntryType.DOCUMENT),
            ("data.json", b'{"key": "value", "num": 42}', "application/json", EntryType.DOCUMENT),
        ]

        v1_path = base / "test_v1.mcpk"

        # 构建 blob 区
        blobs = []
        toc_entries = []
        cursor = 64  # header 后开始
        for name, data, mime, etype in entries_data:
            name_b = name.encode("utf-8")
            mime_b = mime.encode("utf-8")
            crc = binascii.crc32(data) & 0xFFFFFFFF

            blobs.append(data)

            toc_fixed = struct.pack(
                "<BB2sI Q Q Q Q H",
                etype, 0x00, b"\x00\x00",  # type, compression=NONE, reserved
                crc, 1000000000000, len(data), len(data), cursor, len(name_b),
            )
            toc_entry = toc_fixed + name_b + struct.pack("<H", len(mime_b)) + mime_b + struct.pack("<H", 0)
            toc_entries.append(toc_entry)
            cursor += len(data)

        toc_offset = cursor
        toc_data = b"".join(toc_entries)
        toc_size = len(toc_data)

        # Footer
        footer_raw = struct.pack("<4sQI", MAGIC, toc_offset, 0)
        footer_crc = binascii.crc32(footer_raw[:12]) & 0xFFFFFFFF
        footer = struct.pack("<4sQI", MAGIC, toc_offset, footer_crc)

        # v1 Header
        v1_header = struct.pack(
            "<4sHH Q Q Q I I 24s",
            MAGIC, 1, 0, 1000000000000,
            toc_offset, toc_size, len(entries_data),
            sum(len(d) for _, d, _, _ in entries_data),
            b"\x00" * 24,
        )

        with open(v1_path, "wb") as f:
            f.write(v1_header)
            for blob in blobs:
                f.write(blob)
            f.write(toc_data)
            f.write(footer)

        # 用 v2 reader 读取
        with MCPKReader(v1_path) as reader:
            assert reader.version == 1
            assert reader.entry_count == 3
            assert len(reader.magic_entries) == 0
            assert len(reader.groups) == 0
            assert len(reader.relations) == 0
            print(f"  v1 版本检测正确 ✓")

            for name, data, _, _ in entries_data:
                extracted = reader.extract(name)
                assert extracted == data
            print(f"  3 个条目内容全部正确 ✓")

            # inspect 应正常工作
            info = reader.inspect()
            assert info["version"] == 1
            assert info["entry_count"] == 3
            print(f"  inspect 输出正常 ✓")

    print(f"  PASS: {label}")
    return True


def test_09_complex_group_scenario(sizes: dict):
    """复杂分组场景：多类型分组 + 多重关系 + 分组元数据。"""
    label = "复杂分组场景"
    print("\n" + "=" * 60)
    print(f"测试 09: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # 生成文件
        files = {}
        for i in range(1, 4):
            p = base / f"video{i}.mp4"
            gen_mp4_file(p, sizes["video"] // 2, seed=500 + i)
            files[f"video{i}.mp4"] = p

            p = base / f"sub{i}.srt"
            gen_srt_file(p, 20 + i * 5, seed=600 + i)
            files[f"sub{i}.srt"] = p

        p = base / "notes.md"
        gen_text_file(p, sizes["text"], seed=700)
        files["notes.md"] = p

        p = base / "summary.md"
        gen_text_file(p, sizes["text"] // 2, seed=701)
        files["summary.md"] = p

        p = base / "handout.pdf"
        gen_binary_file(p, sizes["image"], seed=702)
        files["handout.pdf"] = p

        mcpk_path = base / "complex.mcpk"

        # 使用显式 create_group API
        with MCPKWriter(mcpk_path) as writer:
            # 课程分组
            g_course = writer.create_group("深度学习入门", GroupType.COURSE,
                metadata={"instructor": "李教授", "semester": "2026春", "credits": 3})

            # 每讲分组
            g1 = writer.create_group("第1讲-神经网络", GroupType.VIDEO_SUBTITLE,
                metadata={"week": 1, "topic": "基础概念"})
            g2 = writer.create_group("第2讲-CNN", GroupType.VIDEO_SUBTITLE,
                metadata={"week": 2, "topic": "卷积网络"})
            g3 = writer.create_group("第3讲-RNN", GroupType.VIDEO_SUBTITLE,
                metadata={"week": 3, "topic": "循环网络"})

            # 学习资料分组
            g_materials = writer.create_group("学习资料", GroupType.DOCUMENT_SET)

            # 添加文件到分组
            writer.add_file(files["video1.mp4"], group=g1)
            writer.add_file(files["sub1.srt"], group=g1)

            writer.add_file(files["video2.mp4"], group=g2)
            writer.add_file(files["sub2.srt"], group=g2)

            writer.add_file(files["video3.mp4"], group=g3)
            writer.add_file(files["sub3.srt"], group=g3)

            writer.add_file(files["notes.md"], group=g_materials)
            writer.add_file(files["summary.md"], group=g_materials)
            writer.add_file(files["handout.pdf"], group=g_materials)

            # 课程内顺序关系
            writer.add_relation("第1讲-神经网络", "第2讲-CNN", RelationType.SEQUEL)
            writer.add_relation("第2讲-CNN", "第3讲-RNN", RelationType.SEQUEL)

            # 课程与资料的引用关系
            writer.add_relation("第1讲-神经网络", "学习资料", RelationType.REFERENCES)
            writer.add_relation("第2讲-CNN", "学习资料", RelationType.REFERENCES)
            writer.add_relation("第3讲-RNN", "学习资料", RelationType.REFERENCES)

        # 验证
        with MCPKReader(mcpk_path) as reader:
            assert len(reader.groups) == 5
            assert len(reader.relations) == 5

            # 验证分组类型
            course_g = reader.find_group("深度学习入门")
            assert course_g.group_type == GroupType.COURSE
            meta = course_g.metadata_dict()
            assert meta["instructor"] == "李教授"
            assert meta["credits"] == 3
            print(f"  课程分组: {course_g.name}, 元数据={meta} ✓")

            for gname in ["第1讲-神经网络", "第2讲-CNN", "第3讲-RNN"]:
                g = reader.find_group(gname)
                assert g is not None
                assert len(g.entry_ids) == 2
                assert g.group_type == GroupType.VIDEO_SUBTITLE
            print(f"  3 讲课程分组, 每组 2 条目 ✓")

            mats = reader.find_group("学习资料")
            assert len(mats.entry_ids) == 3
            assert mats.group_type == GroupType.DOCUMENT_SET
            print(f"  学习资料分组: 3 条目 ✓")

            # 验证关系
            sequel_rels = [r for r in reader.relations if r.relation_type == RelationType.SEQUEL]
            ref_rels = [r for r in reader.relations if r.relation_type == RelationType.REFERENCES]
            assert len(sequel_rels) == 2
            assert len(ref_rels) == 3
            print(f"  关系: 2 SEQUEL + 3 REFERENCES = 5 ✓")

            # 验证内容
            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 ✓")

            # 按分组提取
            extract_dir = base / "complex_extracted"
            for g in reader.groups:
                paths = reader.extract_group(g.name, extract_dir)
                assert len(paths) == len(g.entry_ids)
            print(f"  全部分组提取成功 ✓")

    print(f"  PASS: {label}")
    return True


def test_10_performance_benchmark(sizes: dict):
    """性能基准：大文件打包/读取速度。"""
    label = "性能基准"
    print("\n" + "=" * 60)
    print(f"测试 10: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        files = {}

        # 生成大文件
        print(f"  生成测试文件 (video={_fmt(sizes['video'])}, "
              f"text={_fmt(sizes['text'])}, audio={_fmt(sizes['audio'])})...")
        t0 = time.time()

        vpath = base / "big_video.mp4"
        gen_mp4_file(vpath, sizes["video"], seed=1)
        files["big_video.mp4"] = vpath

        tpath = base / "big_text.md"
        gen_text_file(tpath, sizes["text"], seed=2)
        files["big_text.md"] = tpath

        apath = base / "big_audio.wav"
        gen_wav_file(apath, sizes["audio"], seed=3)
        files["big_audio.wav"] = apath

        jpg_path = base / "big_image.jpg"
        gen_jpeg_file(jpg_path, sizes["image"], seed=4)
        files["big_image.jpg"] = jpg_path

        # 额外的小文件（模拟真实场景中的大量附件）
        small_dir = base / "attachments"
        small_dir.mkdir()
        for i in range(20):
            p = small_dir / f"note_{i:03d}.txt"
            gen_text_file(p, 1000, seed=100 + i)
            files[f"note_{i:03d}.txt"] = p

        gen_time = time.time() - t0
        total_size = sum(p.stat().st_size for p in files.values())
        print(f"  文件生成耗时: {gen_time:.2f}s, 总大小 {_fmt(total_size)}")

        # ── 打包性能 ──
        mcpk_path = base / "benchmark.mcpk"
        t0 = time.time()
        with MCPKWriter(mcpk_path) as writer:
            # 视频+字幕分一组
            writer.add_file(files["big_video.mp4"], group_name="视频内容")
            writer.add_file(files["big_text.md"], group_name="视频内容")

            # 音频独立
            writer.add_file(files["big_audio.wav"])

            # 图片独立
            writer.add_file(files["big_image.jpg"])

            # 小文件全部归一组
            for name in sorted(files.keys()):
                if name.startswith("note_"):
                    writer.add_file(files[name], group_name="附件集")

            writer.add_relation("视频内容", "附件集", RelationType.RELATED)

        pack_time = time.time() - t0
        container_size = mcpk_path.stat().st_size
        print(f"  打包: {len(files)} 文件, {_fmt(total_size)} -> {_fmt(container_size)}")
        print(f"         压缩比 {total_size/max(container_size,1):.2f}x, "
              f"耗时 {pack_time:.2f}s ({total_size/pack_time/1024/1024:.1f} MB/s)")

        # ── 读取+校验性能 ──
        t0 = time.time()
        with MCPKReader(mcpk_path) as reader:
            errors = reader.verify()
        verify_time = time.time() - t0
        print(f"  校验: {len(files)} 文件, 耗时 {verify_time:.2f}s "
              f"({total_size/verify_time/1024/1024:.1f} MB/s)")
        assert not errors

        # ── 随机提取性能 ──
        t0 = time.time()
        with MCPKReader(mcpk_path) as reader:
            data = reader.extract("big_video.mp4")
        extract_time = time.time() - t0
        print(f"  提取最大文件 ({_fmt(len(data))}): {extract_time:.3f}s "
              f"({len(data)/extract_time/1024/1024:.1f} MB/s)")

        # ── 全量提取性能 ──
        t0 = time.time()
        with MCPKReader(mcpk_path) as reader:
            extract_dir = base / "bench_extracted"
            reader.extract_all(extract_dir)
        extract_all_time = time.time() - t0
        print(f"  全量提取: {extract_all_time:.2f}s "
              f"({total_size/extract_all_time/1024/1024:.1f} MB/s)")

        # ── 按分组提取性能 ──
        t0 = time.time()
        with MCPKReader(mcpk_path) as reader:
            extract_dir = base / "group_bench"
            reader.extract_group("附件集", extract_dir)
        group_extract_time = time.time() - t0
        print(f"  分组提取 (附件集): {group_extract_time:.3f}s")

        # 验证内容正确性
        with MCPKReader(mcpk_path) as reader:
            for name, path in files.items():
                extracted = reader.extract(name)
                original = path.read_bytes()
                assert extracted == original
        print(f"  全部 {len(files)} 文件内容验证一致 ✓")

    print(f"  PASS: {label}")
    return True


def test_11_group_blob_ordering(sizes: dict):
    """分组 blob 排序验证：确保 NO_GROUP 条目排在最后。"""
    label = "分组 Blob 排序"
    print("\n" + "=" * 60)
    print(f"测试 11: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # 生成文件：3 组 + 2 个无分组
        files = {}
        for i in range(3):
            p = base / f"grp{i}_a.txt"
            gen_text_file(p, 200, seed=800 + i)
            files[f"grp{i}_a.txt"] = p

            p = base / f"grp{i}_b.txt"
            gen_text_file(p, 200, seed=900 + i)
            files[f"grp{i}_b.txt"] = p

        for i in range(2):
            p = base / f"ungrouped_{i}.txt"
            gen_text_file(p, 150, seed=1000 + i)
            files[f"ungrouped_{i}.txt"] = p

        mcpk_path = base / "ordering.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            # 故意乱序添加，验证排序正确性
            writer.add_file(files["grp2_a.txt"], group_name="组C")
            writer.add_file(files["ungrouped_0.txt"])
            writer.add_file(files["grp0_a.txt"], group_name="组A")
            writer.add_file(files["grp1_b.txt"], group_name="组B")
            writer.add_file(files["grp0_b.txt"], group_name="组A")
            writer.add_file(files["ungrouped_1.txt"])
            writer.add_file(files["grp1_a.txt"], group_name="组B")
            writer.add_file(files["grp2_b.txt"], group_name="组C")

        with MCPKReader(mcpk_path) as reader:
            # 验证：按 group_id 排序，NO_GROUP 排在最后
            group_offsets = []
            for g in reader.groups:
                entries = [reader.entries[eid] for eid in g.entry_ids]
                min_off = min(e.blob_offset for e in entries)
                max_end = max(e.blob_offset + e.stored_size for e in entries)
                group_offsets.append((g.group_id, g.name, min_off, max_end))
                print(f"  {g.name} (id={g.group_id}): blob 范围 [{min_off}, {max_end})")

            ungrouped = [e for e in reader.entries if e.group_id == NO_GROUP]
            if ungrouped:
                ungroup_min = min(e.blob_offset for e in ungrouped)
                print(f"  无分组: blob 范围 [{ungroup_min}, ...)")
            else:
                ungroup_min = float('inf')

            # 按 group_id 排序后验证连续性
            group_offsets.sort(key=lambda x: x[0])
            for i in range(len(group_offsets) - 1):
                _, _, _, curr_end = group_offsets[i]
                _, _, next_start, _ = group_offsets[i + 1]
                assert curr_end <= next_start, \
                    f"{group_offsets[i][1]} 结束 {curr_end} > {group_offsets[i+1][1]} 开始 {next_start}"

            if ungrouped and group_offsets:
                last_end = group_offsets[-1][3]
                assert last_end <= ungroup_min, \
                    f"最后分组结束 {last_end} > 无分组开始 {ungroup_min}"

            order_str = " < ".join(g[1] for g in group_offsets) + " < 无分组"
            print(f"  Blob 排序正确: {order_str} ✓")

            # 内容验证
            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 ✓")

    print(f"  PASS: {label}")
    return True


def test_12_inspect_and_json(sizes: dict):
    """inspect 输出完整性 + JSON 可序列化。"""
    label = "Inspect 与 JSON"
    print("\n" + "=" * 60)
    print(f"测试 12: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        files = build_course_scenario(base, {"video": 2000, "text": 500, "image": 1000, "audio": 5000})

        mcpk_path = base / "inspect.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            for i in range(1, 4):
                gname = f"第{i}讲"
                writer.add_file(files[f"lecture{i}.mp4"], group_name=gname)
                writer.add_file(files[f"lecture{i}.srt"], group_name=gname)
                writer.add_file(files[f"slides{i}.md"], group_name=gname)
            writer.add_relation("第1讲", "第2讲", RelationType.SEQUEL)
            writer.add_relation("第2讲", "第3讲", RelationType.SEQUEL)

        with MCPKReader(mcpk_path) as reader:
            info = reader.inspect()

            # JSON 可序列化
            json_str = json.dumps(info, ensure_ascii=False, indent=2)
            assert len(json_str) > 100
            print(f"  JSON 输出: {len(json_str)} 字符 ✓")

            # 关键字段存在
            assert info["version"] == 2
            assert info["entry_count"] == 9
            assert info["group_count"] == 3
            assert "magic_index_offset" in info
            assert "magic_index_size" in info
            assert "group_index_offset" in info
            assert "group_index_size" in info
            assert len(info["groups"]) == 3
            assert len(info["relations"]) == 2
            print(f"  所有 v2 字段存在 ✓")

            # 分组信息
            for g in info["groups"]:
                assert "group_id" in g
                assert "name" in g
                assert "type" in g
                assert "entry_count" in g
                assert "entry_ids" in g
                assert g["entry_count"] == len(g["entry_ids"])
            print(f"  分组信息完整 ✓")

            # 关系信息
            for r in info["relations"]:
                assert "source" in r
                assert "target" in r
                assert "type" in r
            print(f"  关系信息完整 ✓")

            # 条目信息
            for e in info["entries"]:
                assert "group_id" in e
                assert "name" in e
                assert "type" in e
                assert "crc32" in e
            print(f"  条目信息完整 ✓")

    print(f"  PASS: {label}")
    return True


def test_13_add_data_api(sizes: dict):
    """add_data API：直接从内存数据打包。"""
    label = "add_data API"
    print("\n" + "=" * 60)
    print(f"测试 13: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        mcpk_path = base / "from_data.mcpk"

        # 内存中的数据
        text_data = "这是一段从内存直接打包的文本。\n" * 100
        text_bytes = text_data.encode("utf-8")

        json_data = json.dumps({"status": "ok", "items": list(range(50))},
                               ensure_ascii=False).encode("utf-8")

        binary_data = os.urandom(5000)

        with MCPKWriter(mcpk_path) as writer:
            writer.add_data(text_bytes, "notes.txt",
                          entry_type=EntryType.DOCUMENT,
                          mime_type="text/plain",
                          metadata={"title": "内存文本"})
            writer.add_data(json_data, "response.json",
                          entry_type=EntryType.DOCUMENT,
                          mime_type="application/json")
            writer.add_data(binary_data, "payload.bin",
                          entry_type=EntryType.DOCUMENT,
                          mime_type="application/octet-stream",
                          compression=Compression.NONE)

        with MCPKReader(mcpk_path) as reader:
            assert reader.entry_count == 3
            assert reader.extract("notes.txt") == text_bytes
            assert reader.extract("response.json") == json_data
            assert reader.extract("payload.bin") == binary_data
            print(f"  3 个内存数据条目一致 ✓")

            meta = reader.get_metadata("notes.txt")
            assert meta["title"] == "内存文本"
            print(f"  元数据正确 ✓")

    print(f"  PASS: {label}")
    return True


def test_14_repeated_pack_unpack(sizes: dict):
    """多次打包/解包一致性：确保可重复性。"""
    label = "重复打包一致性"
    print("\n" + "=" * 60)
    print(f"测试 14: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        files = build_mixed_scenario(base, {"video": 2000, "text": 500, "image": 1000, "audio": 3000})

        mcpk_paths = []
        for run in range(3):
            mcpk_path = base / f"run_{run}.mcpk"
            with MCPKWriter(mcpk_path) as writer:
                for name, path in sorted(files.items()):
                    writer.add_file(path, metadata={"run": run})
            mcpk_paths.append(mcpk_path)

        # 每次打包的内容应一致（相同文件 → 相同数据）
        contents = []
        for p in mcpk_paths:
            with MCPKReader(p) as reader:
                extracted = {}
                for name in sorted(files.keys()):
                    extracted[name] = reader.extract(name)
                contents.append(extracted)

        for name in sorted(files.keys()):
            for run in range(1, 3):
                assert contents[0][name] == contents[run][name], \
                    f"Run 0 vs Run {run}: {name} 内容不一致"
        print(f"  3 次打包, 全部 {len(files)} 文件内容一致 ✓")

        # 校验全部
        for p in mcpk_paths:
            with MCPKReader(p) as reader:
                errors = reader.verify()
                assert not errors
        print(f"  3 次打包全部校验通过 ✓")

    print(f"  PASS: {label}")
    return True


def test_15_stress_groups_and_relations(sizes: dict):
    """压力测试：大量分组和关系。"""
    label = "分组/关系压力测试"
    print("\n" + "=" * 60)
    print(f"测试 15: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        num_groups = 20
        files_per_group = 3

        mcpk_path = base / "stress.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            for g in range(num_groups):
                gname = f"group_{g:03d}"
                for f in range(files_per_group):
                    p = base / f"g{g}_f{f}.txt"
                    gen_text_file(p, 200, seed=g * 100 + f)
                    writer.add_file(p, group_name=gname,
                                  metadata={"group": g, "file": f})

            # 链式关系
            for g in range(num_groups - 1):
                writer.add_relation(f"group_{g:03d}", f"group_{g+1:03d}",
                                  RelationType.SEQUEL)

            # 交叉关系
            for g in range(0, num_groups, 3):
                if g + 2 < num_groups:
                    writer.add_relation(f"group_{g:03d}", f"group_{g+2:03d}",
                                      RelationType.RELATED)

        with MCPKReader(mcpk_path) as reader:
            assert len(reader.groups) == num_groups
            expected_entries = num_groups * files_per_group
            assert reader.entry_count == expected_entries
            print(f"  {num_groups} 分组, {expected_entries} 条目 ✓")

            sequel_count = num_groups - 1
            related_count = len(range(0, num_groups, 3))
            # 修正：g+2 < num_groups 条件
            related_count = sum(1 for g in range(0, num_groups, 3) if g + 2 < num_groups)
            assert len(reader.relations) == sequel_count + related_count
            print(f"  {sequel_count} SEQUEL + {related_count} RELATED = "
                  f"{len(reader.relations)} 关系 ✓")

            # 随机验证几个分组
            import random
            rng = random.Random(42)
            sample_groups = rng.sample(range(num_groups), min(5, num_groups))
            for g in sample_groups:
                gname = f"group_{g:03d}"
                entries = reader.list_group_entries(gname)
                assert len(entries) == files_per_group
                for e in entries:
                    data = reader.extract(e.name)
                    assert len(data) == e.original_size
            print(f"  随机验证 {len(sample_groups)} 个分组内容正确 ✓")

            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 ✓")

    print(f"  PASS: {label}")
    return True


def test_16_encrypt_full_roundtrip(sizes: dict):
    """加密（FULL 模式）完整 roundtrip。"""
    label = "加密 FULL 模式"
    print("\n" + "=" * 60)
    print(f"测试 16: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        files = {}
        for name, gen_fn, size_key, seed in [
            ("doc.txt", gen_text_file, "text", 1),
            ("photo.jpg", gen_jpeg_file, "image", 2),
            ("video.mp4", gen_mp4_file, "video", 3),
        ]:
            p = base / name
            gen_fn(p, sizes[size_key], seed=seed)
            files[name] = p

        password = "test_password_2026"
        mcpk_path = base / "encrypted.mcpk"

        # 打包加密
        with MCPKWriter(mcpk_path, password=password) as writer:
            for name, path in files.items():
                writer.add_file(path, metadata={"title": name})

        assert mcpk_path.stat().st_size > 0
        print(f"  加密文件大小: {mcpk_path.stat().st_size} bytes")

        # 读取解密
        with MCPKReader(mcpk_path, password=password) as reader:
            assert reader.version == 2
            assert reader.is_encrypted
            assert reader.encryption_params is not None
            print(f"  加密模式: {EncryptionMode(reader.encryption_params.encrypt_mode).name} ✓")

            errors = reader.verify()
            assert not errors, f"校验失败: {errors}"
            print(f"  完整性校验通过 ✓")

            for name, path in files.items():
                extracted = reader.extract(name)
                original = path.read_bytes()
                assert extracted == original
            print(f"  全部 {len(files)} 文件内容一致 ✓")

        # 密码错误应失败
        try:
            with MCPKReader(mcpk_path, password="wrong_password") as reader:
                pass
            assert False, "应该抛出密码错误异常"
        except MCPKError as e:
            assert "密码错误" in str(e)
            print(f"  密码错误检测正确 ✓")

        # 不提供密码应失败
        try:
            with MCPKReader(mcpk_path) as reader:
                pass
            assert False, "应该抛出缺少密码异常"
        except MCPKError as e:
            assert "密码" in str(e)
            print(f"  缺少密码检测正确 ✓")

    print(f"  PASS: {label}")
    return True


def test_17_encrypt_metadata_only(sizes: dict):
    """加密 METADATA_ONLY 模式。"""
    label = "加密 METADATA_ONLY 模式"
    print("\n" + "=" * 60)
    print(f"测试 17: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        txt_path = base / "notes.txt"
        gen_text_file(txt_path, sizes["text"], seed=10)

        mp4_path = base / "video.mp4"
        gen_mp4_file(mp4_path, sizes["video"], seed=11)

        password = "meta_only_pass"
        mcpk_path = base / "meta_enc.mcpk"

        with MCPKWriter(mcpk_path, password=password,
                        encrypt_mode="metadata_only") as writer:
            writer.add_file(txt_path)
            writer.add_file(mp4_path)

        with MCPKReader(mcpk_path, password=password) as reader:
            assert reader.is_encrypted
            assert reader.encryption_params.encrypt_mode == EncryptionMode.METADATA_ONLY
            print(f"  加密模式: METADATA_ONLY ✓")

            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 ✓")

            for name in ["notes.txt", "video.mp4"]:
                data = reader.extract(name)
                assert len(data) > 0
            print(f"  内容提取正确 ✓")

    print(f"  PASS: {label}")
    return True


def test_18_no_encryption_compat(sizes: dict):
    """不加密模式完全兼容（与 v2.0 行为一致）。"""
    label = "不加密兼容性"
    print("\n" + "=" * 60)
    print(f"测试 18: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        txt_path = base / "plain.txt"
        gen_text_file(txt_path, sizes["text"], seed=20)

        mcpk_path = base / "plain.mcpk"

        # 不加密写入
        with MCPKWriter(mcpk_path) as writer:
            writer.add_file(txt_path)

        # 不加密读取
        with MCPKReader(mcpk_path) as reader:
            assert not reader.is_encrypted
            assert reader.encryption_params is None
            assert reader.version == 2
            print(f"  未加密文件识别正确 ✓")

            data = reader.extract("plain.txt")
            assert data == txt_path.read_bytes()
            print(f"  内容一致 ✓")

            # 时间戳应存在
            entry = reader.find("plain.txt")
            assert entry.created_at > 0
            assert entry.modified_at > 0
            print(f"  时间戳: created={entry.time_info()['created']} ✓")

    print(f"  PASS: {label}")
    return True


def test_19_timestamps(sizes: dict):
    """时间戳验证：created_at / modified_at / packed_at。"""
    label = "时间戳验证"
    print("\n" + "=" * 60)
    print(f"测试 19: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # 创建文件并设置已知时间
        txt_path = base / "timed.txt"
        gen_text_file(txt_path, sizes["text"], seed=30)
        # 设置文件时间为已知值
        known_time = 1700000000.0  # 2023-11-14 22:13:20 UTC
        os.utime(txt_path, (known_time, known_time))

        before_pack = int(time.time() * 1000)
        mcpk_path = base / "timed.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            writer.add_file(txt_path)
        after_pack = int(time.time() * 1000)

        with MCPKReader(mcpk_path) as reader:
            entry = reader.entries[0]
            header = reader.header

            # modified_at 应接近 known_time
            known_ms = int(known_time * 1000)
            assert abs(entry.modified_at - known_ms) < 2000, \
                f"modified_at 偏差过大: {entry.modified_at} vs {known_ms}"
            print(f"  modified_at: {entry.time_info()['modified']} ✓")

            # created_at 应 > 0
            assert entry.created_at > 0
            print(f"  created_at: {entry.time_info()['created']} ✓")

            # packed_at 应在 before/after 之间
            assert before_pack <= header.packed_at <= after_pack
            print(f"  packed_at (header): {header.packed_at_iso()} ✓")

            # inspect 应包含时间信息
            info = reader.inspect()
            assert info["packed_at"] > 0
            assert info["entries"][0]["modified_at"] > 0
            print(f"  inspect 时间字段完整 ✓")

    print(f"  PASS: {label}")
    return True


def test_20_encrypt_with_groups(sizes: dict):
    """加密 + 分组 + 关系 组合测试。"""
    label = "加密+分组组合"
    print("\n" + "=" * 60)
    print(f"测试 20: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        files = {}
        for i in range(1, 3):
            p = base / f"video{i}.mp4"
            gen_mp4_file(p, sizes["video"] // 2, seed=400 + i)
            files[f"video{i}.mp4"] = p
            p = base / f"sub{i}.srt"
            gen_srt_file(p, 20, seed=500 + i)
            files[f"sub{i}.srt"] = p

        password = "group_encrypt_test"
        mcpk_path = base / "enc_grouped.mcpk"

        with MCPKWriter(mcpk_path, password=password) as writer:
            writer.add_file(files["video1.mp4"], group_name="第1讲")
            writer.add_file(files["sub1.srt"], group_name="第1讲")
            writer.add_file(files["video2.mp4"], group_name="第2讲")
            writer.add_file(files["sub2.srt"], group_name="第2讲")
            writer.add_relation("第1讲", "第2讲", RelationType.SEQUEL)

        with MCPKReader(mcpk_path, password=password) as reader:
            assert reader.is_encrypted
            assert len(reader.groups) == 2
            assert len(reader.relations) == 1
            print(f"  加密文件: 2 分组, 1 关系 ✓")

            # 按分组提取
            for g in reader.groups:
                entries = reader.list_group_entries(g.name)
                assert len(entries) == 2
                for e in entries:
                    data = reader.extract(e.name)
                    original = files[e.name].read_bytes()
                    assert data == original
            print(f"  分组内容全部一致 ✓")

            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 ✓")

    print(f"  PASS: {label}")
    return True


# ═══════════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════════

QUICK_TESTS = [
    ("test_01",  test_01_v2_roundtrip_basic,    "tiny"),
    ("test_02",  test_02_grouping_and_relations, "tiny"),
    ("test_03",  test_03_video_types,            "tiny"),
    ("test_04",  test_04_magic_index_correctness,"tiny"),
    ("test_05",  test_05_compression_ratios,     "tiny"),
    ("test_06",  test_06_many_entries,           "tiny"),
    ("test_07",  test_07_edge_cases,             "tiny"),
    ("test_08",  test_08_v1_backward_compat,     "tiny"),
    ("test_09",  test_09_complex_group_scenario, "tiny"),
    ("test_10",  test_10_performance_benchmark,  "tiny"),
    ("test_11",  test_11_group_blob_ordering,    "tiny"),
    ("test_12",  test_12_inspect_and_json,       "tiny"),
    ("test_13",  test_13_add_data_api,           "tiny"),
    ("test_14",  test_14_repeated_pack_unpack,   "tiny"),
    ("test_15",  test_15_stress_groups_and_relations, "tiny"),
    ("test_16",  test_16_encrypt_full_roundtrip, "tiny"),
    ("test_17",  test_17_encrypt_metadata_only,  "tiny"),
    ("test_18",  test_18_no_encryption_compat,   "tiny"),
    ("test_19",  test_19_timestamps,             "tiny"),
    ("test_20",  test_20_encrypt_with_groups,    "tiny"),
]

FULL_TESTS = [
    ("test_01",  test_01_v2_roundtrip_basic,    "medium"),
    ("test_02",  test_02_grouping_and_relations, "medium"),
    ("test_03",  test_03_video_types,            "small"),
    ("test_04",  test_04_magic_index_correctness,"tiny"),
    ("test_05",  test_05_compression_ratios,     "medium"),
    ("test_06",  test_06_many_entries,           "small"),
    ("test_07",  test_07_edge_cases,             "tiny"),
    ("test_08",  test_08_v1_backward_compat,     "tiny"),
    ("test_09",  test_09_complex_group_scenario, "medium"),
    ("test_10",  test_10_performance_benchmark,  "large"),
    ("test_11",  test_11_group_blob_ordering,    "tiny"),
    ("test_12",  test_12_inspect_and_json,       "small"),
    ("test_13",  test_13_add_data_api,           "tiny"),
    ("test_14",  test_14_repeated_pack_unpack,   "small"),
    ("test_15",  test_15_stress_groups_and_relations, "tiny"),
    ("test_16",  test_16_encrypt_full_roundtrip, "small"),
    ("test_17",  test_17_encrypt_metadata_only,  "small"),
    ("test_18",  test_18_no_encryption_compat,   "tiny"),
    ("test_19",  test_19_timestamps,             "tiny"),
    ("test_20",  test_20_encrypt_with_groups,    "small"),
]

LARGE_TESTS = [
    ("test_01",  test_01_v2_roundtrip_basic,    "xlarge"),
    ("test_02",  test_02_grouping_and_relations, "large"),
    ("test_05",  test_05_compression_ratios,     "large"),
    ("test_06",  test_06_many_entries,           "medium"),
    ("test_09",  test_09_complex_group_scenario, "large"),
    ("test_10",  test_10_performance_benchmark,  "xlarge"),
    ("test_15",  test_15_stress_groups_and_relations, "medium"),
    ("test_16",  test_16_encrypt_full_roundtrip, "medium"),
    ("test_19",  test_19_timestamps,             "medium"),
    ("test_20",  test_20_encrypt_with_groups,    "medium"),
]


def main():
    import argparse
    parser = argparse.ArgumentParser(description="MCPK v2 集成测试")
    parser.add_argument("--quick", action="store_true", help="快速模式 (tiny 文件)")
    parser.add_argument("--large-only", action="store_true", help="仅大文件测试")
    parser.add_argument("--size", choices=list(SIZE_PRESETS.keys()),
                       help="手动指定文件大小预设")
    parser.add_argument("--test", type=str, help="仅运行指定测试 (如 test_01)")
    args = parser.parse_args()

    if args.large_only:
        test_plan = LARGE_TESTS
    elif args.quick:
        test_plan = QUICK_TESTS
    else:
        test_plan = FULL_TESTS

    if args.test:
        test_plan = [(tid, fn, sz) for tid, fn, sz in test_plan if tid == args.test]
        if not test_plan:
            print(f"未找到测试: {args.test}")
            sys.exit(1)

    if args.size:
        test_plan = [(tid, fn, args.size) for tid, fn, _ in test_plan]

    mode = "LARGE" if args.large_only else ("QUICK" if args.quick else "FULL")
    size_label = args.size or "auto"
    print(f"MCPK v2 测试套件  [模式={mode}, 预设={size_label}]")
    print(f"测试数量: {len(test_plan)}")
    print()

    results = []
    total_start = time.time()

    for tid, test_fn, preset in test_plan:
        sizes = SIZE_PRESETS[preset]
        try:
            ok = test_fn(sizes)
            results.append((tid, test_fn.__name__, ok, preset))
        except Exception as e:
            print(f"\n  EXCEPTION: {e}")
            import traceback
            traceback.print_exc()
            results.append((tid, test_fn.__name__, False, preset))

    total_time = time.time() - total_start

    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    for tid, name, ok, preset in results:
        status = "PASS" if ok else "FAIL"
        short = name.replace("test_", "").split("_", 1)[-1] if "_" in name else name
        print(f"  [{status}] {tid}: {short}  ({preset})")

    passed = sum(1 for _, _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n  {passed}/{len(results)} 通过, {failed} 失败, 总耗时 {total_time:.1f}s")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
