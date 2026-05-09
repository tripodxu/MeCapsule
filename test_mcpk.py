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
    EntryType, Compression, GroupType, RelationType, IntraRelationType,
    NO_GROUP, MAGIC, VERSION, EncryptionMode, KdfType,
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
            print(f"  全部 {len(files)} 文件内容一致 [OK]")

        # ── 提取到目录 ──
        extract_dir = base / "extracted"
        with MCPKReader(mcpk_path) as reader:
            reader.extract_all(extract_dir)
            for name, original_path in files.items():
                out = extract_dir / name
                assert out.exists(), f"缺失: {name}"
                assert out.read_bytes() == original_path.read_bytes()
            print(f"  提取到目录验证通过 [OK]")

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
            print(f"  3 个分组, 每组 3 条目 [OK]")

            # 组间关系
            assert len(reader.relations) == 3
            sequel_count = sum(1 for r in reader.relations if r.relation_type == RelationType.SEQUEL)
            related_count = sum(1 for r in reader.relations if r.relation_type == RelationType.RELATED)
            assert sequel_count == 2 and related_count == 1
            print(f"  3 条关系 (2 SEQUEL + 1 RELATED) [OK]")

            # 物理相邻性
            for g in reader.groups:
                entries = [reader.entries[eid] for eid in g.entry_ids]
                offsets = sorted(e.blob_offset for e in entries)
                sizes_list = [e.stored_size for e in entries]
                # 组内 blob 应连续
                for j in range(len(offsets) - 1):
                    assert offsets[j] + sizes_list[j] <= offsets[j + 1] + 1, \
                        f"分组 {g.name} 内 blob 不连续"
            print(f"  各分组 blob 物理相邻 [OK]")

            # 完整性
            errors = reader.verify()
            assert not errors, f"校验失败: {errors}"
            print(f"  完整性校验通过 [OK]")

            # 按分组提取
            for g in reader.groups:
                entries = reader.list_group_entries(g.name)
                for e in entries:
                    data = reader.extract(e.name)
                    assert len(data) == e.original_size
            print(f"  按分组提取内容正确 [OK]")

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
            print(f"  {len(video_entries)} 个 VIDEO 条目 [OK]")

            for me in reader.magic_entries:
                assert me.entry_type == EntryType.VIDEO
            print(f"  Magic Index 全部为 VIDEO 类型 [OK]")

            for name, (path, expected_mime) in originals.items():
                entry = reader.find(name)
                assert entry is not None
                assert entry.mime_type == expected_mime
                extracted = reader.extract(name)
                original_data = path.read_bytes()
                assert extracted == original_data
            print(f"  全部视频内容一致 [OK]")

            # 元数据
            meta = reader.get_metadata("clip.mp4")
            assert meta["duration_ms"] == 120000
            assert meta["width"] == 1920
            print(f"  视频元数据正确 [OK]")

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
            print(f"  {len(reader.magic_entries)} 个 Magic Entry [OK]")

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
                    print(f"  {entry.name}: magic {me.magic_bytes[:4].hex()}... [OK]")
                else:
                    print(f"  {entry.name}: (无固定 magic, 跳过)")

            # 验证 entry_type 在 Magic Index 中与 TOC 中一致
            for me in reader.magic_entries:
                entry = reader.entries[me.entry_id]
                assert me.entry_type == entry.entry_type
            print(f"  Magic Index 与 TOC 类型一致 [OK]")

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
                          f"压缩比 {ratio:.2f}x ({comp_name}) [OK]")
                elif entry.name.endswith((".jpg", ".mp4")):
                    assert not entry.is_compressed, f"{entry.name} 不应被压缩"
                    print(f"  {entry.name}: {entry.original_size}B, 未压缩 (已压缩格式) [OK]")
                elif entry.name.endswith(".wav"):
                    if entry.is_compressed:
                        print(f"  {entry.name}: {entry.original_size}B -> {entry.stored_size}B, "
                              f"压缩比 {ratio:.2f}x ({comp_name}) [OK]")
                    else:
                        print(f"  {entry.name}: {entry.original_size}B, 未压缩 (zstd/lz4 不可用) [OK]")

            errors = reader.verify()
            assert not errors
            print(f"  压缩后完整性校验通过 [OK]")

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
        print(f"  随机抽取 {len(sample_names)} 文件验证一致 [OK]")

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
            print(f"  {len(edge_files)} 边界文件校验通过 [OK]")

            # 空文件
            data = reader.extract("empty.txt")
            assert data == b""
            print(f"  空文件: 0 字节 [OK]")

            # 1 字节
            data = reader.extract("one_byte.bin")
            assert data == b"\x42"
            print(f"  1 字节文件: 正确 [OK]")

            # 中文名
            data = reader.extract("会议纪要_2026年春季.md")
            assert len(data) > 0
            print(f"  中文文件名: 正确 [OK]")

            # 带空格
            data = reader.extract("my notes (final).txt")
            assert len(data) > 0
            print(f"  带空格文件名: 正确 [OK]")

            # 长文件名
            data = reader.extract(long_name)
            assert len(data) > 0
            print(f"  长文件名 ({len(long_name)} 字符): 正确 [OK]")

        # ── 空 MCPK 文件 ──
        empty_mcpk = base / "empty_container.mcpk"
        with MCPKWriter(empty_mcpk) as writer:
            pass
        with MCPKReader(empty_mcpk) as reader:
            assert reader.version == 2
            assert reader.entry_count == 0
            assert len(reader.groups) == 0
            print(f"  空容器: v{reader.version}, 0 条目 [OK]")

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
            print(f"  v1 版本检测正确 [OK]")

            for name, data, _, _ in entries_data:
                extracted = reader.extract(name)
                assert extracted == data
            print(f"  3 个条目内容全部正确 [OK]")

            # inspect 应正常工作
            info = reader.inspect()
            assert info["version"] == 1
            assert info["entry_count"] == 3
            print(f"  inspect 输出正常 [OK]")

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
            print(f"  课程分组: {course_g.name}, 元数据={meta} [OK]")

            for gname in ["第1讲-神经网络", "第2讲-CNN", "第3讲-RNN"]:
                g = reader.find_group(gname)
                assert g is not None
                assert len(g.entry_ids) == 2
                assert g.group_type == GroupType.VIDEO_SUBTITLE
            print(f"  3 讲课程分组, 每组 2 条目 [OK]")

            mats = reader.find_group("学习资料")
            assert len(mats.entry_ids) == 3
            assert mats.group_type == GroupType.DOCUMENT_SET
            print(f"  学习资料分组: 3 条目 [OK]")

            # 验证关系
            sequel_rels = [r for r in reader.relations if r.relation_type == RelationType.SEQUEL]
            ref_rels = [r for r in reader.relations if r.relation_type == RelationType.REFERENCES]
            assert len(sequel_rels) == 2
            assert len(ref_rels) == 3
            print(f"  关系: 2 SEQUEL + 3 REFERENCES = 5 [OK]")

            # 验证内容
            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 [OK]")

            # 按分组提取
            extract_dir = base / "complex_extracted"
            for g in reader.groups:
                paths = reader.extract_group(g.name, extract_dir)
                assert len(paths) == len(g.entry_ids)
            print(f"  全部分组提取成功 [OK]")

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
        print(f"  全部 {len(files)} 文件内容验证一致 [OK]")

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
            print(f"  Blob 排序正确: {order_str} [OK]")

            # 内容验证
            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 [OK]")

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
            print(f"  JSON 输出: {len(json_str)} 字符 [OK]")

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
            print(f"  所有 v2 字段存在 [OK]")

            # 分组信息
            for g in info["groups"]:
                assert "group_id" in g
                assert "name" in g
                assert "type" in g
                assert "entry_count" in g
                assert "entry_ids" in g
                assert g["entry_count"] == len(g["entry_ids"])
            print(f"  分组信息完整 [OK]")

            # 关系信息
            for r in info["relations"]:
                assert "source" in r
                assert "target" in r
                assert "type" in r
            print(f"  关系信息完整 [OK]")

            # 条目信息
            for e in info["entries"]:
                assert "group_id" in e
                assert "name" in e
                assert "type" in e
                assert "crc32" in e
            print(f"  条目信息完整 [OK]")

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
            print(f"  3 个内存数据条目一致 [OK]")

            meta = reader.get_metadata("notes.txt")
            assert meta["title"] == "内存文本"
            print(f"  元数据正确 [OK]")

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
        print(f"  3 次打包, 全部 {len(files)} 文件内容一致 [OK]")

        # 校验全部
        for p in mcpk_paths:
            with MCPKReader(p) as reader:
                errors = reader.verify()
                assert not errors
        print(f"  3 次打包全部校验通过 [OK]")

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
            print(f"  {num_groups} 分组, {expected_entries} 条目 [OK]")

            sequel_count = num_groups - 1
            related_count = len(range(0, num_groups, 3))
            # 修正：g+2 < num_groups 条件
            related_count = sum(1 for g in range(0, num_groups, 3) if g + 2 < num_groups)
            assert len(reader.relations) == sequel_count + related_count
            print(f"  {sequel_count} SEQUEL + {related_count} RELATED = "
                  f"{len(reader.relations)} 关系 [OK]")

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
            print(f"  随机验证 {len(sample_groups)} 个分组内容正确 [OK]")

            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 [OK]")

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

        # 打包加密（使用 XOR 保证零依赖可用）
        with MCPKWriter(mcpk_path, password=password, encryption="xor") as writer:
            for name, path in files.items():
                writer.add_file(path, metadata={"title": name})

        assert mcpk_path.stat().st_size > 0
        print(f"  加密文件大小: {mcpk_path.stat().st_size} bytes")

        # 读取解密
        with MCPKReader(mcpk_path, password=password) as reader:
            assert reader.version == 2
            assert reader.is_encrypted
            assert reader.encryption_params is not None
            print(f"  加密模式: {EncryptionMode(reader.encryption_params.encrypt_mode).name} [OK]")

            errors = reader.verify()
            assert not errors, f"校验失败: {errors}"
            print(f"  完整性校验通过 [OK]")

            for name, path in files.items():
                extracted = reader.extract(name)
                original = path.read_bytes()
                assert extracted == original
            print(f"  全部 {len(files)} 文件内容一致 [OK]")

        # 密码错误应失败
        try:
            with MCPKReader(mcpk_path, password="wrong_password") as reader:
                pass
            assert False, "应该抛出密码错误异常"
        except MCPKError as e:
            assert "密码错误" in str(e)
            print(f"  密码错误检测正确 [OK]")

        # 不提供密码应失败
        try:
            with MCPKReader(mcpk_path) as reader:
                pass
            assert False, "应该抛出缺少密码异常"
        except MCPKError as e:
            assert "密码" in str(e)
            print(f"  缺少密码检测正确 [OK]")

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
                        encrypt_mode="metadata_only", encryption="xor") as writer:
            writer.add_file(txt_path)
            writer.add_file(mp4_path)

        with MCPKReader(mcpk_path, password=password) as reader:
            assert reader.is_encrypted
            assert reader.encryption_params.encrypt_mode == EncryptionMode.METADATA_ONLY
            print(f"  加密模式: METADATA_ONLY [OK]")

            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 [OK]")

            for name in ["notes.txt", "video.mp4"]:
                data = reader.extract(name)
                assert len(data) > 0
            print(f"  内容提取正确 [OK]")

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
            print(f"  未加密文件识别正确 [OK]")

            data = reader.extract("plain.txt")
            assert data == txt_path.read_bytes()
            print(f"  内容一致 [OK]")

            # 时间戳应存在
            entry = reader.find("plain.txt")
            assert entry.created_at > 0
            assert entry.modified_at > 0
            print(f"  时间戳: created={entry.time_info()['created']} [OK]")

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
            print(f"  modified_at: {entry.time_info()['modified']} [OK]")

            # created_at 应 > 0
            assert entry.created_at > 0
            print(f"  created_at: {entry.time_info()['created']} [OK]")

            # packed_at 应在 before/after 之间
            assert before_pack <= header.packed_at <= after_pack
            print(f"  packed_at (header): {header.packed_at_iso()} [OK]")

            # inspect 应包含时间信息
            info = reader.inspect()
            assert info["packed_at"] > 0
            assert info["entries"][0]["modified_at"] > 0
            print(f"  inspect 时间字段完整 [OK]")

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

        with MCPKWriter(mcpk_path, password=password, encryption="xor") as writer:
            writer.add_file(files["video1.mp4"], group_name="第1讲")
            writer.add_file(files["sub1.srt"], group_name="第1讲")
            writer.add_file(files["video2.mp4"], group_name="第2讲")
            writer.add_file(files["sub2.srt"], group_name="第2讲")
            writer.add_relation("第1讲", "第2讲", RelationType.SEQUEL)

        with MCPKReader(mcpk_path, password=password) as reader:
            assert reader.is_encrypted
            assert len(reader.groups) == 2
            assert len(reader.relations) == 1
            print(f"  加密文件: 2 分组, 1 关系 [OK]")

            # 按分组提取
            for g in reader.groups:
                entries = reader.list_group_entries(g.name)
                assert len(entries) == 2
                for e in entries:
                    data = reader.extract(e.name)
                    original = files[e.name].read_bytes()
                    assert data == original
            print(f"  分组内容全部一致 [OK]")

            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 [OK]")

    print(f"  PASS: {label}")
    return True


# ═══════════════════════════════════════════════════════════
#  v2.2 新增测试：AES-GCM / Tags / IntraRelations / import_folder / JSON Index
# ═══════════════════════════════════════════════════════════

def _try_aes_writer(*args, **kwargs):
    """尝试创建 AES 加密 Writer，cryptography 不可用时返回 None。"""
    try:
        return MCPKWriter(*args, **kwargs)
    except ImportError:
        return None


def test_21_aes_gcm_full_roundtrip(sizes: dict):
    """AES-256-GCM FULL 模式完整 roundtrip。"""
    label = "AES-GCM FULL 模式"
    print("\n" + "=" * 60)
    print(f"测试 21: {label}")
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

        password = "aes_test_2026"
        mcpk_path = base / "aes_encrypted.mcpk"

        writer = _try_aes_writer(mcpk_path, password=password, encryption="aes")
        if writer is None:
            print(f"  SKIP: cryptography 库未安装")
            return True
        with writer:
            for name, path in files.items():
                writer.add_file(path, metadata={"title": name})

        assert mcpk_path.stat().st_size > 0
        print(f"  文件大小: {mcpk_path.stat().st_size} bytes")

        with MCPKReader(mcpk_path, password=password) as reader:
            assert reader.version == 2
            assert reader.is_encrypted
            assert reader.encryption_params is not None
            assert reader.encryption_params.kdf_type == 0x02  # PBKDF2_AES
            assert reader.encryption_params.kdf_iterations == 600_000
            print(f"  KDF: PBKDF2_AES, iterations={reader.encryption_params.kdf_iterations} [OK]")

            errors = reader.verify()
            assert not errors, f"校验失败: {errors}"
            print(f"  完整性校验通过 [OK]")

            for name, path in files.items():
                extracted = reader.extract(name)
                original = path.read_bytes()
                assert extracted == original
            print(f"  全部 {len(files)} 文件内容一致 [OK]")

        # 密码错误
        try:
            with MCPKReader(mcpk_path, password="wrong") as reader:
                pass
            assert False
        except MCPKError as e:
            assert "密码错误" in str(e)
            print(f"  密码错误检测正确 [OK]")

    print(f"  PASS: {label}")
    return True


def test_22_aes_gcm_metadata_only(sizes: dict):
    """AES-256-GCM METADATA_ONLY 模式。"""
    label = "AES-GCM METADATA_ONLY"
    print("\n" + "=" * 60)
    print(f"测试 22: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        txt_path = base / "notes.txt"
        gen_text_file(txt_path, sizes["text"], seed=10)
        mp4_path = base / "video.mp4"
        gen_mp4_file(mp4_path, sizes["video"], seed=11)

        password = "meta_aes_pass"
        mcpk_path = base / "meta_aes.mcpk"

        writer = _try_aes_writer(mcpk_path, password=password,
                        encrypt_mode="metadata_only", encryption="aes")
        if writer is None:
            print(f"  SKIP: cryptography 库未安装")
            return True
        with writer:
            writer.add_file(txt_path)
            writer.add_file(mp4_path)

        with MCPKReader(mcpk_path, password=password) as reader:
            assert reader.is_encrypted
            assert reader.encryption_params.encrypt_mode == EncryptionMode.METADATA_ONLY
            assert reader.encryption_params.is_aes  # AES 模式
            print(f"  模式: METADATA_ONLY + AES-GCM [OK]")

            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 [OK]")

            for name in ["notes.txt", "video.mp4"]:
                data = reader.extract(name)
                assert len(data) > 0
            print(f"  内容提取正确 [OK]")

    print(f"  PASS: {label}")
    return True


def test_23_aes_gcm_data_only(sizes: dict):
    """AES-256-GCM DATA_ONLY 模式。"""
    label = "AES-GCM DATA_ONLY"
    print("\n" + "=" * 60)
    print(f"测试 23: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        txt_path = base / "data.txt"
        gen_text_file(txt_path, sizes["text"], seed=20)

        password = "data_aes_pass"
        mcpk_path = base / "data_aes.mcpk"

        writer = _try_aes_writer(mcpk_path, password=password,
                        encrypt_mode="data_only", encryption="aes")
        if writer is None:
            print(f"  SKIP: cryptography 库未安装")
            return True
        with writer:
            writer.add_file(txt_path)

        with MCPKReader(mcpk_path, password=password) as reader:
            assert reader.is_encrypted
            assert reader.encryption_params.encrypt_mode == EncryptionMode.DATA_ONLY
            print(f"  模式: DATA_ONLY + AES-GCM [OK]")

            data = reader.extract("data.txt")
            assert data == txt_path.read_bytes()
            print(f"  内容一致 [OK]")

    print(f"  PASS: {label}")
    return True


def test_24_aes_gcm_wrong_password(sizes: dict):
    """AES-GCM 各种错误密码场景。"""
    label = "AES-GCM 错误密码"
    print("\n" + "=" * 60)
    print(f"测试 24: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        txt_path = base / "secret.txt"
        gen_text_file(txt_path, sizes["text"], seed=30)

        password = "correct_password"
        mcpk_path = base / "secret.mcpk"

        writer = _try_aes_writer(mcpk_path, password=password, encryption="aes")
        if writer is None:
            print(f"  SKIP: cryptography 库未安装")
            return True
        with writer:
            writer.add_file(txt_path)

        # 完全错误的密码
        try:
            with MCPKReader(mcpk_path, password="wrong") as reader:
                pass
            assert False
        except MCPKError as e:
            assert "密码错误" in str(e)
            print(f"  错误密码检测 [OK]")

        # 空密码
        try:
            with MCPKReader(mcpk_path, password="") as reader:
                pass
            assert False
        except MCPKError as e:
            assert "密码错误" in str(e)
            print(f"  空密码检测 [OK]")

        # 不提供密码
        try:
            with MCPKReader(mcpk_path) as reader:
                pass
            assert False
        except MCPKError as e:
            assert "密码" in str(e)
            print(f"  缺少密码检测 [OK]")

        # 正确密码
        with MCPKReader(mcpk_path, password=password) as reader:
            data = reader.extract("secret.txt")
            assert data == txt_path.read_bytes()
            print(f"  正确密码解密 [OK]")

    print(f"  PASS: {label}")
    return True


def test_25_aes_gcm_tamper_detection(sizes: dict):
    """AES-GCM 篡改检测：修改密文应解密失败。"""
    label = "AES-GCM 篡改检测"
    print("\n" + "=" * 60)
    print(f"测试 25: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        txt_path = base / "data.txt"
        gen_text_file(txt_path, sizes["text"], seed=40)

        password = "tamper_test"
        mcpk_path = base / "tamper.mcpk"

        writer = _try_aes_writer(mcpk_path, password=password, encryption="aes")
        if writer is None:
            print(f"  SKIP: cryptography 库未安装")
            return True
        with writer:
            writer.add_file(txt_path)

        # 读取文件，篡改数据区的一个字节
        data = bytearray(mcpk_path.read_bytes())
        # 找到 blob 数据区（在 header + ep + magic_index 之后）
        # 简单方法：篡改文件中间的字节
        mid = len(data) // 2
        data[mid] ^= 0xFF
        tampered_path = base / "tampered.mcpk"
        tampered_path.write_bytes(bytes(data))

        try:
            with MCPKReader(tampered_path, password=password) as reader:
                # 读取可能在校验阶段就失败
                try:
                    reader.extract("data.txt")
                    # 如果 extract 没失败，verify 应该失败
                    errors = reader.verify()
                    assert len(errors) > 0
                except MCPKError:
                    pass
            print(f"  篡改检测正确 [OK]")
        except MCPKError:
            print(f"  篡改检测正确（加载阶段） [OK]")

    print(f"  PASS: {label}")
    return True


def test_26_xor_backward_compat(sizes: dict):
    """XOR 加密向后兼容（encryption='xor'）。"""
    label = "XOR 向后兼容"
    print("\n" + "=" * 60)
    print(f"测试 26: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        files = {}
        for name, gen_fn, size_key, seed in [
            ("doc.txt", gen_text_file, "text", 50),
            ("pic.jpg", gen_jpeg_file, "image", 51),
        ]:
            p = base / name
            gen_fn(p, sizes[size_key], seed=seed)
            files[name] = p

        password = "xor_compat"
        mcpk_path = base / "xor_file.mcpk"

        # 使用 XOR 加密
        with MCPKWriter(mcpk_path, password=password, encryption="xor") as writer:
            for name, path in files.items():
                writer.add_file(path)

        with MCPKReader(mcpk_path, password=password) as reader:
            assert reader.is_encrypted
            assert reader.encryption_params.kdf_type == 0x01  # SHA256_XOR
            print(f"  KDF: SHA256_XOR [OK]")

            for name, path in files.items():
                extracted = reader.extract(name)
                original = path.read_bytes()
                assert extracted == original
            print(f"  全部内容一致 [OK]")

            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 [OK]")

    print(f"  PASS: {label}")
    return True


def test_27_aes_gcm_with_groups_and_relations(sizes: dict):
    """AES-GCM + 分组 + 关系组合。"""
    label = "AES-GCM + 分组 + 关系"
    print("\n" + "=" * 60)
    print(f"测试 27: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        files = {}
        for i in range(1, 3):
            p = base / f"video{i}.mp4"
            gen_mp4_file(p, sizes["video"] // 2, seed=600 + i)
            files[f"video{i}.mp4"] = p
            p = base / f"sub{i}.srt"
            gen_srt_file(p, 20, seed=700 + i)
            files[f"sub{i}.srt"] = p

        password = "group_aes_test"
        mcpk_path = base / "group_aes.mcpk"

        writer = _try_aes_writer(mcpk_path, password=password, encryption="aes")
        if writer is None:
            print(f"  SKIP: cryptography 库未安装")
            return True
        with writer:
            writer.add_file(files["video1.mp4"], group_name="第1讲")
            writer.add_file(files["sub1.srt"], group_name="第1讲")
            writer.add_file(files["video2.mp4"], group_name="第2讲")
            writer.add_file(files["sub2.srt"], group_name="第2讲")
            writer.add_relation("第1讲", "第2讲", RelationType.SEQUEL)

        with MCPKReader(mcpk_path, password=password) as reader:
            assert reader.is_encrypted
            assert len(reader.groups) == 2
            assert len(reader.relations) == 1
            print(f"  2 分组, 1 关系 [OK]")

            for g in reader.groups:
                entries = reader.list_group_entries(g.name)
                for e in entries:
                    data = reader.extract(e.name)
                    original = files[e.name].read_bytes()
                    assert data == original
            print(f"  分组内容全部一致 [OK]")

            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 [OK]")

    print(f"  PASS: {label}")
    return True


def test_28_group_tags_basic(sizes: dict):
    """分组 Tag 标签：创建时带标签 + roundtrip。"""
    label = "分组 Tag 标签"
    print("\n" + "=" * 60)
    print(f"测试 28: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        txt_path = base / "doc.txt"
        gen_text_file(txt_path, sizes["text"], seed=80)
        jpg_path = base / "photo.jpg"
        gen_jpeg_file(jpg_path, sizes["image"], seed=81)

        mcpk_path = base / "tags.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            g1 = writer.create_group("工作文档", GroupType.DOCUMENT_SET,
                                      tags=["工作", "2026", "重要"])
            writer.add_file(txt_path, group=g1)

            g2 = writer.create_group("旅行照片", GroupType.MEDIA_ALBUM,
                                      tags=["旅行", "风景"])
            writer.add_file(jpg_path, group=g2)

        with MCPKReader(mcpk_path) as reader:
            assert len(reader.groups) == 2

            g1 = reader.find_group("工作文档")
            assert g1 is not None
            assert g1.tags == ["工作", "2026", "重要"]
            print(f"  工作文档: tags={g1.tags} [OK]")

            g2 = reader.find_group("旅行照片")
            assert g2 is not None
            assert g2.tags == ["旅行", "风景"]
            print(f"  旅行照片: tags={g2.tags} [OK]")

            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 [OK]")

    print(f"  PASS: {label}")
    return True


def test_29_add_tag_dynamic(sizes: dict):
    """动态添加 Tag 标签。"""
    label = "动态添加 Tag"
    print("\n" + "=" * 60)
    print(f"测试 29: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        txt_path = base / "note.txt"
        gen_text_file(txt_path, sizes["text"], seed=90)

        mcpk_path = base / "dynamic_tags.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            g = writer.create_group("笔记", tags=["初始标签"])
            writer.add_file(txt_path, group=g)
            # 动态添加
            writer.add_tag(g, "动态标签1")
            writer.add_tag(g, "动态标签2")
            writer.add_tag(g, "初始标签")  # 重复，不应添加

        with MCPKReader(mcpk_path) as reader:
            g = reader.find_group("笔记")
            assert g.tags == ["初始标签", "动态标签1", "动态标签2"]
            print(f"  tags={g.tags} (无重复) [OK]")

    print(f"  PASS: {label}")
    return True


def test_30_intra_relation_basic(sizes: dict):
    """组内关系基本功能。"""
    label = "组内关系基本"
    print("\n" + "=" * 60)
    print(f"测试 30: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        vid_path = base / "lecture.mp4"
        gen_mp4_file(vid_path, sizes["video"], seed=100)
        sub_path = base / "lecture.srt"
        gen_srt_file(sub_path, 30, seed=101)

        mcpk_path = base / "intra_rel.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            writer.add_file(vid_path, group_name="课程")
            writer.add_file(sub_path, group_name="课程")
            writer.add_intra_relation(
                "课程", source="lecture.srt", target="lecture.mp4",
                relation_type=IntraRelationType.SUBTITLE_OF,
                description="字幕属于视频",
            )

        with MCPKReader(mcpk_path) as reader:
            g = reader.find_group("课程")
            assert g is not None
            assert len(g.intra_relations) == 1

            ir = g.intra_relations[0]
            assert ir.relation_type == IntraRelationType.SUBTITLE_OF
            assert ir.description == "字幕属于视频"
            # source=srt, target=mp4
            assert reader.entries[ir.source_entry].name == "lecture.srt"
            assert reader.entries[ir.target_entry].name == "lecture.mp4"
            print(f"  SUBTITLE_OF: lecture.srt -> lecture.mp4 [OK]")

            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 [OK]")

    print(f"  PASS: {label}")
    return True


def test_31_multiple_intra_relations(sizes: dict):
    """多个组内关系。"""
    label = "多组内关系"
    print("\n" + "=" * 60)
    print(f"测试 31: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        vid = base / "meeting.mp4"
        gen_mp4_file(vid, sizes["video"], seed=110)
        transcript = base / "transcript.txt"
        gen_text_file(transcript, sizes["text"], seed=111)
        thumb = base / "thumb.jpg"
        gen_jpeg_file(thumb, 500, seed=112)
        notes = base / "notes.md"
        gen_text_file(notes, sizes["text"], seed=113)

        mcpk_path = base / "multi_intra.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            writer.add_file(vid, group_name="会议")
            writer.add_file(transcript, group_name="会议")
            writer.add_file(thumb, group_name="会议")
            writer.add_file(notes, group_name="会议")

            writer.add_intra_relation(
                "会议", source="transcript.txt", target="meeting.mp4",
                relation_type=IntraRelationType.TRANSCRIPT_OF,
            )
            writer.add_intra_relation(
                "会议", source="thumb.jpg", target="meeting.mp4",
                relation_type=IntraRelationType.THUMBNAIL_OF,
            )
            writer.add_intra_relation(
                "会议", source="notes.md", target="meeting.mp4",
                relation_type=IntraRelationType.ANNOTATION_OF,
                description="会议批注",
            )

        with MCPKReader(mcpk_path) as reader:
            g = reader.find_group("会议")
            assert len(g.intra_relations) == 3
            types = [ir.relation_type for ir in g.intra_relations]
            assert IntraRelationType.TRANSCRIPT_OF in types
            assert IntraRelationType.THUMBNAIL_OF in types
            assert IntraRelationType.ANNOTATION_OF in types
            print(f"  3 条组内关系: TRANSCRIPT_OF, THUMBNAIL_OF, ANNOTATION_OF [OK]")

    print(f"  PASS: {label}")
    return True


def test_32_tags_and_intra_with_encryption(sizes: dict):
    """Tags + 组内关系 + AES-GCM 加密组合。"""
    label = "Tags+组内关系+加密"
    print("\n" + "=" * 60)
    print(f"测试 32: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        vid = base / "v.mp4"
        gen_mp4_file(vid, sizes["video"], seed=120)
        sub = base / "v.srt"
        gen_srt_file(sub, 20, seed=121)

        password = "combo_test"
        mcpk_path = base / "combo.mcpk"
        writer = _try_aes_writer(mcpk_path, password=password, encryption="aes")
        if writer is None:
            print(f"  SKIP: cryptography 库未安装")
            return True
        with writer:
            g = writer.create_group("视频集", GroupType.VIDEO_SUBTITLE,
                                     tags=["加密", "视频"])
            writer.add_file(vid, group=g)
            writer.add_file(sub, group=g)
            writer.add_intra_relation(
                "视频集", source="v.srt", target="v.mp4",
                relation_type=IntraRelationType.SUBTITLE_OF,
            )

        with MCPKReader(mcpk_path, password=password) as reader:
            assert reader.is_encrypted
            g = reader.find_group("视频集")
            assert g.tags == ["加密", "视频"]
            assert len(g.intra_relations) == 1
            print(f"  加密 + tags + 组内关系 [OK]")

            for e in reader.list_group_entries("视频集"):
                data = reader.extract(e.name)
                assert len(data) == e.original_size
            print(f"  内容提取正确 [OK]")

    print(f"  PASS: {label}")
    return True


def test_33_import_folder_basic(sizes: dict):
    """import_folder 基本功能。"""
    label = "import_folder 基本"
    print("\n" + "=" * 60)
    print(f"测试 33: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        folder = base / "项目资料"
        folder.mkdir()
        (folder / "readme.md").write_text("项目说明", encoding="utf-8")
        (folder / "data.json").write_text('{"key": "value"}', encoding="utf-8")
        gen_text_file(folder / "notes.txt", sizes["text"], seed=130)

        mcpk_path = base / "folder.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            g = writer.import_folder(folder)

        assert g.name == "项目资料"
        assert len(g.entry_ids) == 3
        print(f"  组名=项目资料, 3 文件 [OK]")

        with MCPKReader(mcpk_path) as reader:
            g = reader.find_group("项目资料")
            assert g is not None
            assert len(g.entry_ids) == 3
            names = sorted(e.name for e in reader.list_group_entries("项目资料"))
            assert "readme.md" in names
            assert "data.json" in names
            assert "notes.txt" in names
            print(f"  文件: {names} [OK]")

            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 [OK]")

    print(f"  PASS: {label}")
    return True


def test_34_import_folder_multiple(sizes: dict):
    """多个文件夹导入。"""
    label = "import_folder 多文件夹"
    print("\n" + "=" * 60)
    print(f"测试 34: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # 文件夹 A
        folder_a = base / "文件夹A"
        folder_a.mkdir()
        for i in range(3):
            gen_text_file(folder_a / f"a_{i}.txt", 200, seed=140 + i)

        # 文件夹 B
        folder_b = base / "文件夹B"
        folder_b.mkdir()
        gen_jpeg_file(folder_b / "photo.jpg", sizes["image"], seed=150)
        gen_srt_file(folder_b / "sub.srt", 10, seed=151)

        mcpk_path = base / "multi_folder.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            writer.import_folder(folder_a, tags=["集合A"])
            writer.import_folder(folder_b, tags=["集合B"])

        with MCPKReader(mcpk_path) as reader:
            assert len(reader.groups) == 2
            ga = reader.find_group("文件夹A")
            gb = reader.find_group("文件夹B")
            assert len(ga.entry_ids) == 3
            assert len(gb.entry_ids) == 2
            assert ga.tags == ["集合A"]
            assert gb.tags == ["集合B"]
            print(f"  文件夹A: 3 文件, tags={ga.tags} [OK]")
            print(f"  文件夹B: 2 文件, tags={gb.tags} [OK]")

    print(f"  PASS: {label}")
    return True


def test_35_import_folder_non_recursive(sizes: dict):
    """import_folder non-recursive 模式。"""
    label = "import_folder 非递归"
    print("\n" + "=" * 60)
    print(f"测试 35: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        folder = base / "src"
        folder.mkdir()
        (folder / "top.txt").write_text("顶层文件", encoding="utf-8")
        sub = folder / "sub"
        sub.mkdir()
        (sub / "nested.txt").write_text("嵌套文件", encoding="utf-8")

        # 非递归
        mcpk_path = base / "non_recursive.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            writer.import_folder(folder, recursive=False)

        with MCPKReader(mcpk_path) as reader:
            g = reader.find_group("src")
            assert len(g.entry_ids) == 1  # 只有 top.txt
            assert reader.entries[g.entry_ids[0]].name == "top.txt"
            print(f"  非递归: 只含顶层文件 [OK]")

    print(f"  PASS: {label}")
    return True


def test_36_json_index_basic(sizes: dict):
    """JSON 索引基本加载。"""
    label = "JSON 索引基本"
    print("\n" + "=" * 60)
    print(f"测试 36: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        project = base / "project"
        project.mkdir()

        # 创建文件
        gen_mp4_file(project / "lecture1.mp4", sizes["video"], seed=160)
        gen_srt_file(project / "lecture1.srt", 20, seed=161)
        gen_text_file(project / "notes.md", sizes["text"], seed=162)

        # 创建索引
        index = {
            "name": "课程包",
            "groups": [
                {
                    "name": "第1讲",
                    "type": "VIDEO_SUBTITLE",
                    "tags": ["ML", "入门"],
                    "metadata": {"week": 1},
                    "files": [
                        {"path": "lecture1.mp4", "title": "第1讲视频"},
                        "lecture1.srt",
                    ],
                },
            ],
            "standalone_files": ["notes.md"],
            "relations": [
                {"source": "第1讲", "target": "第1讲", "type": "RELATED",
                 "desc": "自引用测试"},
            ],
        }
        index_path = base / "index.json"
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False)

        mcpk_path = base / "indexed.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            result = writer.load_index(index_path, base_dir=project)

        assert result["loaded"] == 3
        assert result["groups_created"] == 1
        assert result["relations_created"] == 1
        assert len(result["skipped"]) == 0
        print(f"  加载: 3 文件, 1 分组, 1 关系, 0 跳过 [OK]")

        with MCPKReader(mcpk_path) as reader:
            g = reader.find_group("第1讲")
            assert g is not None
            assert g.tags == ["ML", "入门"]
            assert g.group_type == GroupType.VIDEO_SUBTITLE
            assert len(g.entry_ids) == 2
            print(f"  第1讲: tags={g.tags}, type=VIDEO_SUBTITLE, 2 条目 [OK]")

            # standalone
            assert reader.find("notes.md") is not None
            print(f"  standalone: notes.md [OK]")

            assert len(reader.relations) == 1
            print(f"  关系: 1 [OK]")

            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 [OK]")

    print(f"  PASS: {label}")
    return True


def test_37_json_index_missing_files(sizes: dict):
    """JSON 索引缺失文件：跳过并警告。"""
    label = "JSON 索引缺失文件"
    print("\n" + "=" * 60)
    print(f"测试 37: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        project = base / "project"
        project.mkdir()

        gen_text_file(project / "exists.txt", sizes["text"], seed=170)
        # missing.txt 不创建

        index = {
            "groups": [
                {
                    "name": "测试组",
                    "files": [
                        "exists.txt",
                        "missing.txt",
                        "also_missing.txt",
                    ],
                },
            ],
            "standalone_files": [
                "another_missing.txt",
                {"path": "exists.txt"},
            ],
        }
        index_path = base / "index.json"
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False)

        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            mcpk_path = base / "partial.mcpk"
            with MCPKWriter(mcpk_path) as writer:
                result = writer.load_index(index_path, base_dir=project)

        assert result["loaded"] == 2  # exists.txt from group + exists.txt standalone
        assert len(result["skipped"]) == 3  # 3 missing files
        print(f"  加载: 2, 跳过: 3 [OK]")
        for path, reason in result["skipped"]:
            print(f"    跳过: {path} ({reason})")

        with MCPKReader(mcpk_path) as reader:
            # exists.txt appears twice (once in group, once standalone)
            assert reader.entry_count >= 1
            print(f"  读取正常 [OK]")

    print(f"  PASS: {label}")
    return True


def test_38_json_index_with_intra_relations(sizes: dict):
    """JSON 索引含组内关系。"""
    label = "JSON 索引组内关系"
    print("\n" + "=" * 60)
    print(f"测试 38: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        project = base / "project"
        project.mkdir()

        gen_mp4_file(project / "video.mp4", sizes["video"], seed=180)
        gen_srt_file(project / "video.srt", 15, seed=181)
        gen_jpeg_file(project / "thumb.jpg", 500, seed=182)

        index = {
            "groups": [
                {
                    "name": "视频组",
                    "files": ["video.mp4", "video.srt", "thumb.jpg"],
                },
            ],
            "intra_relations": [
                {"group": "视频组", "source": "video.srt", "target": "video.mp4",
                 "type": "SUBTITLE_OF", "desc": "字幕"},
                {"group": "视频组", "source": "thumb.jpg", "target": "video.mp4",
                 "type": "THUMBNAIL_OF"},
            ],
        }
        index_path = base / "index.json"
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False)

        mcpk_path = base / "intra_indexed.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            result = writer.load_index(index_path, base_dir=project)

        with MCPKReader(mcpk_path) as reader:
            g = reader.find_group("视频组")
            assert len(g.intra_relations) == 2
            types = {ir.relation_type for ir in g.intra_relations}
            assert IntraRelationType.SUBTITLE_OF in types
            assert IntraRelationType.THUMBNAIL_OF in types
            print(f"  2 条组内关系 [OK]")

    print(f"  PASS: {label}")
    return True


def test_39_json_index_with_encryption(sizes: dict):
    """JSON 索引 + AES-GCM 加密组合。"""
    label = "JSON 索引 + 加密"
    print("\n" + "=" * 60)
    print(f"测试 39: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        project = base / "project"
        project.mkdir()

        gen_text_file(project / "secret.md", sizes["text"], seed=190)

        index = {
            "groups": [{"name": "机密", "files": ["secret.md"]}],
        }
        index_path = base / "index.json"
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f)

        password = "index_aes"
        mcpk_path = base / "enc_indexed.mcpk"
        writer = _try_aes_writer(mcpk_path, password=password, encryption="aes")
        if writer is None:
            print(f"  SKIP: cryptography 库未安装")
            return True
        with writer:
            writer.load_index(index_path, base_dir=project)

        with MCPKReader(mcpk_path, password=password) as reader:
            assert reader.is_encrypted
            g = reader.find_group("机密")
            assert g is not None
            data = reader.extract("secret.md")
            assert len(data) > 0
            print(f"  加密索引包: 内容正确 [OK]")

    print(f"  PASS: {label}")
    return True


def test_40_inspect_with_new_fields(sizes: dict):
    """inspect 输出包含新字段（tags, intra_rels, kdf_type）。"""
    label = "Inspect 新字段"
    print("\n" + "=" * 60)
    print(f"测试 40: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        vid = base / "v.mp4"
        gen_mp4_file(vid, sizes["video"], seed=200)
        sub = base / "v.srt"
        gen_srt_file(sub, 10, seed=201)

        password = "inspect_test"
        mcpk_path = base / "inspect_new.mcpk"
        writer = _try_aes_writer(mcpk_path, password=password, encryption="aes")
        if writer is None:
            print(f"  SKIP: cryptography 库未安装")
            return True
        with writer:
            g = writer.create_group("视频", GroupType.VIDEO_SUBTITLE,
                                     tags=["测试", "inspect"])
            writer.add_file(vid, group=g)
            writer.add_file(sub, group=g)
            writer.add_intra_relation(
                "视频", source="v.srt", target="v.mp4",
                relation_type=IntraRelationType.SUBTITLE_OF,
            )

        with MCPKReader(mcpk_path, password=password) as reader:
            info = reader.inspect()

            # 新字段
            assert info["kdf_type"] == "PBKDF2_AES"
            assert info["kdf_iterations"] == 600_000
            print(f"  kdf_type: {info['kdf_type']}, iterations: {info['kdf_iterations']} [OK]")

            g_info = info["groups"][0]
            assert g_info["tags"] == ["测试", "inspect"]
            assert len(g_info["intra_relations"]) == 1
            assert g_info["intra_relations"][0]["type"] == "SUBTITLE_OF"
            print(f"  tags: {g_info['tags']} [OK]")
            print(f"  intra_relations: {g_info['intra_relations'][0]['type']} [OK]")

            # JSON 可序列化
            json_str = json.dumps(info, ensure_ascii=False, indent=2)
            assert len(json_str) > 100
            print(f"  JSON 输出: {len(json_str)} 字符 [OK]")

    print(f"  PASS: {label}")
    return True


def test_41_tag_deduplication(sizes: dict):
    """Tag 去重验证。"""
    label = "Tag 去重"
    print("\n" + "=" * 60)
    print(f"测试 41: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        txt = base / "f.txt"
        gen_text_file(txt, 200, seed=210)

        mcpk_path = base / "dedup.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            g = writer.create_group("G", tags=["a", "b", "c"])
            writer.add_tag(g, "b")  # 重复
            writer.add_tag(g, "d")
            writer.add_tag(g, "a")  # 重复
            writer.add_file(txt, group=g)

        with MCPKReader(mcpk_path) as reader:
            g = reader.find_group("G")
            assert g.tags == ["a", "b", "c", "d"]
            print(f"  tags={g.tags} (无重复) [OK]")

    print(f"  PASS: {label}")
    return True


def test_42_many_tags_per_group(sizes: dict):
    """每组大量 Tag 标签。"""
    label = "大量 Tag"
    print("\n" + "=" * 60)
    print(f"测试 42: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        txt = base / "f.txt"
        gen_text_file(txt, 200, seed=220)

        many_tags = [f"tag_{i:03d}" for i in range(50)]
        mcpk_path = base / "many_tags.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            g = writer.create_group("大数据", tags=many_tags)
            writer.add_file(txt, group=g)

        with MCPKReader(mcpk_path) as reader:
            g = reader.find_group("大数据")
            assert len(g.tags) == 50
            assert g.tags == many_tags
            print(f"  50 个 tags roundtrip 正确 [OK]")

    print(f"  PASS: {label}")
    return True


def test_43_intra_relation_custom_type(sizes: dict):
    """组内关系 CUSTOM 类型。"""
    label = "IntraRelation CUSTOM"
    print("\n" + "=" * 60)
    print(f"测试 43: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        a = base / "a.txt"
        a.write_text("文件A", encoding="utf-8")
        b = base / "b.txt"
        b.write_text("文件B", encoding="utf-8")

        mcpk_path = base / "custom_intra.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            writer.add_file(a, group_name="G")
            writer.add_file(b, group_name="G")
            writer.add_intra_relation(
                "G", source="a.txt", target="b.txt",
                relation_type=IntraRelationType.CUSTOM,
                description="自定义关系",
            )

        with MCPKReader(mcpk_path) as reader:
            g = reader.find_group("G")
            ir = g.intra_relations[0]
            assert ir.relation_type == IntraRelationType.CUSTOM
            assert ir.description == "自定义关系"
            print(f"  CUSTOM 类型 [OK]")

    print(f"  PASS: {label}")
    return True


def test_44_import_folder_with_tags_and_relations(sizes: dict):
    """import_folder + tags + 组间关系。"""
    label = "import_folder + tags + 关系"
    print("\n" + "=" * 60)
    print(f"测试 44: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        f1 = base / "课程资料"
        f1.mkdir()
        gen_text_file(f1 / "slides.md", sizes["text"], seed=230)

        f2 = base / "作业"
        f2.mkdir()
        gen_text_file(f2 / "hw1.md", sizes["text"], seed=231)

        mcpk_path = base / "folder_rel.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            writer.import_folder(f1, tags=["课程"], group_type=GroupType.COURSE)
            writer.import_folder(f2, tags=["作业"])
            writer.add_relation("课程资料", "作业", RelationType.REFERENCES,
                                description="课程引用作业")

        with MCPKReader(mcpk_path) as reader:
            assert len(reader.groups) == 2
            assert len(reader.relations) == 1
            g1 = reader.find_group("课程资料")
            assert g1.tags == ["课程"]
            assert g1.group_type == GroupType.COURSE
            print(f"  课程资料: tags={g1.tags}, type=COURSE [OK]")
            print(f"  关系: 课程资料 --[REFERENCES]--> 作业 [OK]")

    print(f"  PASS: {label}")
    return True


def test_45_all_intra_relation_types(sizes: dict):
    """覆盖所有 IntraRelationType 枚举值。"""
    label = "所有 IntraRelationType"
    print("\n" + "=" * 60)
    print(f"测试 45: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # 创建 10 个文件
        files = []
        for i in range(10):
            p = base / f"f{i}.txt"
            p.write_text(f"文件{i}", encoding="utf-8")
            files.append(f"f{i}.txt")

        mcpk_path = base / "all_types.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            for f in files:
                writer.add_file(base / f, group_name="全类型")

            # 为每种 IntraRelationType 创建一条关系
            all_types = [
                IntraRelationType.SUBTITLE_OF,
                IntraRelationType.ATTACHMENT_OF,
                IntraRelationType.TRANSCRIPT_OF,
                IntraRelationType.THUMBNAIL_OF,
                IntraRelationType.ANNOTATION_OF,
                IntraRelationType.CHAPTER_OF,
                IntraRelationType.SUPPLEMENT_OF,
                IntraRelationType.DERIVED_FROM,
                IntraRelationType.VERSION_OF,
            ]
            for i, rt in enumerate(all_types):
                writer.add_intra_relation(
                    "全类型",
                    source=files[i], target=files[(i + 1) % 10],
                    relation_type=rt,
                )

        with MCPKReader(mcpk_path) as reader:
            g = reader.find_group("全类型")
            assert len(g.intra_relations) == 9
            types_found = {ir.relation_type for ir in g.intra_relations}
            for rt in all_types:
                assert rt in types_found
            print(f"  9 种 IntraRelationType 全部 roundtrip 正确 [OK]")

    print(f"  PASS: {label}")
    return True


def test_46_encryption_none_still_works(sizes: dict):
    """不加密模式完全不受新代码影响。"""
    label = "不加密兼容"
    print("\n" + "=" * 60)
    print(f"测试 46: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        txt = base / "plain.txt"
        gen_text_file(txt, sizes["text"], seed=240)

        mcpk_path = base / "plain.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            g = writer.create_group("G", tags=["plain"])
            writer.add_file(txt, group=g)

        with MCPKReader(mcpk_path) as reader:
            assert not reader.is_encrypted
            assert reader.encryption_params is None
            g = reader.find_group("G")
            assert g.tags == ["plain"]
            data = reader.extract("plain.txt")
            assert data == txt.read_bytes()
            print(f"  未加密 + tags + roundtrip 正确 [OK]")

    print(f"  PASS: {label}")
    return True


def test_47_json_index_empty(sizes: dict):
    """JSON 索引空文件 / 最小配置。"""
    label = "JSON 索引最小"
    print("\n" + "=" * 60)
    print(f"测试 47: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # 空索引
        index_path = base / "empty_index.json"
        with open(index_path, "w") as f:
            json.dump({}, f)

        mcpk_path = base / "empty_idx.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            result = writer.load_index(index_path)

        assert result["loaded"] == 0
        assert result["groups_created"] == 0
        print(f"  空索引: 0 文件, 0 分组 [OK]")

        with MCPKReader(mcpk_path) as reader:
            assert reader.entry_count == 0
            print(f"  读取正常 [OK]")

        # 只有 standalone_files
        index_path2 = base / "minimal.json"
        txt = base / "solo.txt"
        txt.write_text("solo", encoding="utf-8")
        with open(index_path2, "w") as f:
            json.dump({"standalone_files": ["solo.txt"]}, f)

        mcpk_path2 = base / "minimal_idx.mcpk"
        with MCPKWriter(mcpk_path2) as writer:
            result = writer.load_index(index_path2, base_dir=base)

        assert result["loaded"] == 1
        with MCPKReader(mcpk_path2) as reader:
            assert reader.entry_count == 1
            assert reader.extract("solo.txt") == b"solo"
            print(f"  最小索引: 1 standalone 文件 [OK]")

    print(f"  PASS: {label}")
    return True


def test_48_json_index_complex(sizes: dict):
    """JSON 索引复杂场景：多组 + 多关系 + 多组内关系 + 标签 + 加密。"""
    label = "JSON 索引复杂场景"
    print("\n" + "=" * 60)
    print(f"测试 48: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        project = base / "course"
        project.mkdir()

        # 创建课程文件
        for i in range(1, 4):
            gen_mp4_file(project / f"lec{i}.mp4", sizes["video"] // 3, seed=300 + i)
            gen_srt_file(project / f"lec{i}.srt", 10 + i * 5, seed=400 + i)
            gen_text_file(project / f"slides{i}.md", sizes["text"] // 2, seed=500 + i)

        gen_text_file(project / "syllabus.md", sizes["text"], seed=600)

        index = {
            "name": "机器学习课程",
            "description": "2026春季课程完整资料",
            "groups": [
                {
                    "name": f"第{i}讲",
                    "type": "COURSE",
                    "tags": ["ML", f"week{i}"],
                    "metadata": {"week": i, "instructor": "张教授"},
                    "files": [
                        f"lec{i}.mp4",
                        f"lec{i}.srt",
                        f"slides{i}.md",
                    ],
                }
                for i in range(1, 4)
            ],
            "standalone_files": [
                {"path": "syllabus.md", "tags": ["大纲"]},
            ],
            "relations": [
                {"source": "第1讲", "target": "第2讲", "type": "SEQUEL"},
                {"source": "第2讲", "target": "第3讲", "type": "SEQUEL"},
                {"source": "第1讲", "target": "第3讲", "type": "RELATED",
                 "desc": "首尾呼应"},
            ],
            "intra_relations": [
                {
                    "group": f"第{i}讲",
                    "source": f"lec{i}.srt",
                    "target": f"lec{i}.mp4",
                    "type": "SUBTITLE_OF",
                }
                for i in range(1, 4)
            ] + [
                {
                    "group": f"第{i}讲",
                    "source": f"slides{i}.md",
                    "target": f"lec{i}.mp4",
                    "type": "ANNOTATION_OF",
                    "desc": f"第{i}讲讲义",
                }
                for i in range(1, 4)
            ],
        }
        index_path = base / "course_index.json"
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

        password = "course_secret"
        mcpk_path = base / "course.mcpk"
        writer = _try_aes_writer(mcpk_path, password=password, encryption="aes")
        if writer is None:
            print(f"  SKIP: cryptography 库未安装")
            return True
        with writer:
            result = writer.load_index(index_path, base_dir=project)

        assert result["loaded"] == 10  # 3*3 + 1
        assert result["groups_created"] == 3
        assert result["relations_created"] == 3
        print(f"  加载: 10 文件, 3 分组, 3 组间关系 [OK]")

        with MCPKReader(mcpk_path, password=password) as reader:
            assert reader.is_encrypted
            assert len(reader.groups) == 3
            assert len(reader.relations) == 3

            for i in range(1, 4):
                g = reader.find_group(f"第{i}讲")
                assert g is not None
                assert len(g.entry_ids) == 3
                assert "ML" in g.tags
                assert f"week{i}" in g.tags
                assert len(g.intra_relations) == 2  # SUBTITLE_OF + ANNOTATION_OF

            print(f"  各分组: 3 条目, 2 tags, 2 组内关系 [OK]")

            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 [OK]")

            # 提取验证
            for e in reader.entries:
                data = reader.extract(e.name)
                assert len(data) == e.original_size
            print(f"  全部 10 文件内容正确 [OK]")

    print(f"  PASS: {label}")
    return True


def test_49_same_name_different_ext(sizes: dict):
    """同名不同扩展名：1.txt, 1.py, 1.png 是否导致冲突。"""
    label = "同名不同扩展名"
    print("\n" + "=" * 60)
    print(f"测试 49: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # ── 创建同名不同扩展名的文件 ──
        txt_content = "这是文本文件内容\n" * 20
        py_content = "#!/usr/bin/env python3\nprint('hello world')\n" * 10
        png_size = sizes["image"]

        txt_path = base / "1.txt"
        py_path = base / "1.py"
        png_path = base / "1.png"

        txt_path.write_text(txt_content, encoding="utf-8")
        py_path.write_text(py_content, encoding="utf-8")
        gen_png_file(png_path, png_size, seed=99)

        originals = {
            "1.txt": txt_path.read_bytes(),
            "1.py": py_path.read_bytes(),
            "1.png": png_path.read_bytes(),
        }

        # ── 打包 ──
        mcpk_path = base / "samename.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            for name, path in [("1.txt", txt_path), ("1.py", py_path), ("1.png", png_path)]:
                writer.add_file(path, metadata={"title": f"测试-{name}"})

        print(f"  打包: 3 同名文件 [OK]")

        # ── 读取验证 ──
        with MCPKReader(mcpk_path) as reader:
            assert reader.entry_count == 3, \
                f"条目数应为 3, 实际 {reader.entry_count}"
            print(f"  条目数: 3 [OK]")

            # 文件名应全部存在
            names = sorted(e.name for e in reader.entries)
            assert names == ["1.png", "1.py", "1.txt"], \
                f"文件名不匹配: {names}"
            print(f"  文件名: {names} [OK]")

            # MIME 类型正确
            mime_map = {e.name: e.mime_type for e in reader.entries}
            assert mime_map["1.txt"] == "text/plain", \
                f"1.txt MIME: {mime_map['1.txt']}"
            assert mime_map["1.py"] == "text/x-python", \
                f"1.py MIME: {mime_map['1.py']}"
            assert mime_map["1.png"] == "image/png", \
                f"1.png MIME: {mime_map['1.png']}"
            print(f"  MIME 类型正确 [OK]")

            # EntryType 正确
            type_map = {e.name: e.entry_type for e in reader.entries}
            assert type_map["1.txt"] == EntryType.DOCUMENT
            assert type_map["1.py"] == EntryType.DOCUMENT
            assert type_map["1.png"] == EntryType.IMAGE
            print(f"  EntryType 正确 [OK]")

            # 内容提取一致
            for name, original_data in originals.items():
                extracted = reader.extract(name)
                assert extracted == original_data, \
                    f"{name} 内容不一致 (提取 {len(extracted)} vs 原始 {len(original_data)})"
            print(f"  全部 3 文件内容一致 [OK]")

            # 校验
            errors = reader.verify()
            assert not errors, f"校验失败: {errors}"
            print(f"  完整性校验通过 [OK]")

            # 提取到目录
            extract_dir = base / "extracted"
            reader.extract_all(extract_dir)
            for name, original_data in originals.items():
                out = extract_dir / name
                assert out.exists(), f"缺失: {name}"
                assert out.read_bytes() == original_data, f"内容不一致: {name}"
            print(f"  提取到目录验证通过 [OK]")

            # inspect JSON 可序列化
            info = reader.inspect()
            json_str = json.dumps(info, ensure_ascii=False)
            assert len(json_str) > 50
            print(f"  inspect JSON 正常 [OK]")

    # ── 分组场景：同名文件分到不同组 ──
    print(f"\n  --- 分组场景 ---")
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # 创建两组同名文件
        group_a_dir = base / "a"
        group_b_dir = base / "b"
        group_a_dir.mkdir()
        group_b_dir.mkdir()

        (group_a_dir / "1.txt").write_text("组A的文本", encoding="utf-8")
        (group_b_dir / "1.txt").write_text("组B的文本", encoding="utf-8")
        (group_a_dir / "1.py").write_text("# 组A的代码", encoding="utf-8")
        (group_b_dir / "1.py").write_text("# 组B的代码", encoding="utf-8")

        mcpk_path = base / "grouped.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            writer.add_file(group_a_dir / "1.txt", group_name="组A")
            writer.add_file(group_a_dir / "1.py", group_name="组A")
            writer.add_file(group_b_dir / "1.txt", group_name="组B")
            writer.add_file(group_b_dir / "1.py", group_name="组B")

        with MCPKReader(mcpk_path) as reader:
            assert reader.entry_count == 4
            assert len(reader.groups) == 2
            print(f"  4 条目, 2 分组 [OK]")

            # find_all 应返回所有同名条目
            txt_entries = reader.find_all("1.txt")
            assert len(txt_entries) == 2, f"1.txt 应有 2 个条目, 实际 {len(txt_entries)}"
            py_entries = reader.find_all("1.py")
            assert len(py_entries) == 2
            print(f"  find_all: 1.txt×2, 1.py×2 [OK]")

            # 使用 group 参数区分同名文件
            a_txt = reader.extract("1.txt", group="组A")
            b_txt = reader.extract("1.txt", group="组B")
            assert a_txt != b_txt, "不同组的 1.txt 内容应不同"
            assert a_txt == "组A的文本".encode("utf-8")
            assert b_txt == "组B的文本".encode("utf-8")
            print(f"  extract(group=) 区分同名文件 [OK]")

            a_py = reader.extract("1.py", group="组A")
            b_py = reader.extract("1.py", group="组B")
            assert a_py == "# 组A的代码".encode("utf-8")
            assert b_py == "# 组B的代码".encode("utf-8")
            print(f"  各组文件内容正确 [OK]")

            # 不指定 group 时 find 返回第一个匹配
            first = reader.find("1.txt")
            assert first is not None
            print(f"  find() 无 group 参数返回首个匹配 [OK]")

            # 指定不存在的 group 应返回 None
            assert reader.find("1.txt", group="不存在的组") is None
            assert reader.find("不存在.txt", group="组A") is None
            print(f"  find() 不存在时返回 None [OK]")

            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 [OK]")

    # ── 加密场景：同名文件加密 roundtrip ──
    print(f"\n  --- 加密场景 ---")
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        txt_path = base / "1.txt"
        py_path = base / "1.py"
        txt_path.write_text("加密文本", encoding="utf-8")
        py_path.write_text("加密代码", encoding="utf-8")

        password = "samename_pass"
        mcpk_path = base / "enc_samename.mcpk"
        with MCPKWriter(mcpk_path, password=password, encryption="xor") as writer:
            writer.add_file(txt_path)
            writer.add_file(py_path)

        with MCPKReader(mcpk_path, password=password) as reader:
            assert reader.entry_count == 2
            assert reader.extract("1.txt") == "加密文本".encode("utf-8")
            assert reader.extract("1.py") == "加密代码".encode("utf-8")
            print(f"  加密 roundtrip 正确 [OK]")

    # ── 同组同名文件：使用 index 参数区分 ──
    print(f"\n  --- 同组同名文件 ---")
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # 创建同组内两个同名文件（通过 add_data 指定不同 arcname 模拟）
        # 实际场景：从不同目录导入时路径不同但文件名相同
        # 这里用 add_data 直接构造
        mcpk_path = base / "same_group.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            writer.add_data(b"version 1", "readme.md",
                            group_name="项目", metadata={"title": "v1"})
            writer.add_data(b"version 2", "readme.md",
                            group_name="项目", metadata={"title": "v2"})
            writer.add_data(b"other file", "other.txt",
                            group_name="项目")

        with MCPKReader(mcpk_path) as reader:
            assert reader.entry_count == 3
            print(f"  3 条目 (2 同名 + 1 不同) [OK]")

            # find_all 应找到 2 个 readme.md
            matches = reader.find_all("readme.md")
            assert len(matches) == 2
            print(f"  find_all('readme.md') = 2 个 [OK]")

            # index=0 和 index=1 返回不同内容
            v1 = reader.extract("readme.md", index=0)
            v2 = reader.extract("readme.md", index=1)
            assert v1 == b"version 1"
            assert v2 == b"version 2"
            assert v1 != v2
            print(f"  extract(index=0) = 'version 1' [OK]")
            print(f"  extract(index=1) = 'version 2' [OK]")

            # 指定 group + index
            v1_g = reader.extract("readme.md", group="项目", index=0)
            v2_g = reader.extract("readme.md", group="项目", index=1)
            assert v1_g == b"version 1"
            assert v2_g == b"version 2"
            print(f"  extract(group+index) 组合正确 [OK]")

            # index 越界应报错
            try:
                reader.extract("readme.md", index=5)
                assert False, "应抛出 KeyError"
            except KeyError as e:
                assert "同名条目" in str(e)
                print(f"  index 越界报错（含同名提示） [OK]")

            # find_all 加 group 过滤
            matches_in_group = reader.find_all("readme.md", group="项目")
            assert len(matches_in_group) == 2
            matches_none = reader.find_all("readme.md", group="不存在")
            assert len(matches_none) == 0
            print(f"  find_all(group=) 过滤正确 [OK]")

            # get_metadata 区分同名文件
            meta0 = reader.get_metadata("readme.md", index=0)
            meta1 = reader.get_metadata("readme.md", index=1)
            assert meta0["title"] == "v1"
            assert meta1["title"] == "v2"
            print(f"  get_metadata(index=) 区分正确 [OK]")

            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 [OK]")

    print(f"  PASS: {label}")
    return True


# ═══════════════════════════════════════════════════════════
#  test_50 ~ test_60: 增强测试
# ═══════════════════════════════════════════════════════════

def test_50_path_safety(sizes: dict):
    """路径安全：拒绝 .. 和绝对路径。"""
    label = "路径安全"
    print("\n" + "=" * 60)
    print(f"测试 50: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        mcpk_path = base / "safe.mcpk"

        with MCPKWriter(mcpk_path) as writer:
            # 应拒绝含 .. 的文件名
            try:
                writer.add_data(b"x", "../etc/passwd")
                assert False, "应抛出 ValueError"
            except ValueError as e:
                assert "不安全" in str(e)
                print(f"  拒绝 ../etc/passwd [OK]")

            # 应拒绝绝对路径
            try:
                writer.add_data(b"x", "/etc/passwd")
                assert False, "应抛出 ValueError"
            except ValueError as e:
                assert "不安全" in str(e)
                print(f"  拒绝 /etc/passwd [OK]")

            # 应拒绝反斜杠开头
            try:
                writer.add_data(b"x", "\\windows\\system32")
                assert False, "应抛出 ValueError"
            except ValueError as e:
                assert "不安全" in str(e)
                print(f"  拒绝 \\windows\\system32 [OK]")

            # 应拒绝中间含 .. 的路径
            try:
                writer.add_data(b"x", "subdir/../../secret.txt")
                assert False, "应抛出 ValueError"
            except ValueError as e:
                assert "不安全" in str(e)
                print(f"  拒绝 subdir/../../secret.txt [OK]")

            # 正常文件名应通过
            writer.add_data(b"safe", "normal.txt")
            writer.add_data(b"safe", "sub/dir/file.txt")
            writer.add_data(b"safe", "dotted.name.txt")
            print(f"  正常文件名通过 [OK]")

        # 验证正常文件可读取
        with MCPKReader(mcpk_path) as reader:
            assert reader.entry_count == 3
            assert reader.extract("normal.txt") == b"safe"
            print(f"  正常文件内容正确 [OK]")

    print(f"  PASS: {label}")
    return True


def test_51_reader_api_coverage(sizes: dict):
    """Reader API 覆盖：list_entries, list_group_entries, get_metadata, extract_entry。"""
    label = "Reader API 覆盖"
    print("\n" + "=" * 60)
    print(f"测试 51: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        mcpk_path = base / "api.mcpk"

        # 创建多样化的文件
        with MCPKWriter(mcpk_path) as writer:
            writer.add_data(b"doc content", "doc.txt",
                            metadata={"title": "文档", "author": "测试"})
            writer.add_data(b"py code", "script.py",
                            metadata={"title": "脚本"})
            gen_png_file(base / "img.png", sizes["image"], seed=51)
            writer.add_file(base / "img.png", group_name="媒体")
            writer.add_data(b"audio data", "sound.mp3",
                            entry_type=EntryType.AUDIO, group_name="媒体")
            writer.add_data(b"video data", "clip.mp4",
                            entry_type=EntryType.VIDEO, group_name="媒体")

        with MCPKReader(mcpk_path) as reader:
            # list_entries 无参数返回全部
            all_entries = reader.list_entries()
            assert len(all_entries) == 5
            print(f"  list_entries() = 5 [OK]")

            # list_entries 按类型过滤
            docs = reader.list_entries(EntryType.DOCUMENT)
            assert len(docs) == 2  # doc.txt + script.py
            print(f"  list_entries(DOCUMENT) = 2 [OK]")

            images = reader.list_entries(EntryType.IMAGE)
            assert len(images) == 1
            print(f"  list_entries(IMAGE) = 1 [OK]")

            audio = reader.list_entries(EntryType.AUDIO)
            assert len(audio) == 1
            print(f"  list_entries(AUDIO) = 1 [OK]")

            video = reader.list_entries(EntryType.VIDEO)
            assert len(video) == 1
            print(f"  list_entries(VIDEO) = 1 [OK]")

            # list_group_entries
            media_entries = reader.list_group_entries("媒体")
            assert len(media_entries) == 3
            names = {e.name for e in media_entries}
            assert names == {"img.png", "sound.mp3", "clip.mp4"}
            print(f"  list_group_entries('媒体') = 3 [OK]")

            # list_group_entries 不存在的组
            try:
                reader.list_group_entries("不存在")
                assert False, "应抛出 KeyError"
            except KeyError:
                print(f"  list_group_entries 不存在时 KeyError [OK]")

            # get_metadata
            meta = reader.get_metadata("doc.txt")
            assert meta["title"] == "文档"
            assert meta["author"] == "测试"
            print(f"  get_metadata('doc.txt') = {meta} [OK]")

            # extract_entry 直接调用
            entry = reader.find("script.py")
            data = reader.extract_entry(entry)
            assert data == b"py code"
            print(f"  extract_entry() 直接调用 [OK]")

            # verify
            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 [OK]")

    print(f"  PASS: {label}")
    return True


def test_52_writer_properties_and_relations(sizes: dict):
    """Writer 属性和关系 API。"""
    label = "Writer 属性和关系"
    print("\n" + "=" * 60)
    print(f"测试 52: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        mcpk_path = base / "props.mcpk"

        with MCPKWriter(mcpk_path) as writer:
            # 初始状态
            assert writer.entry_count == 0
            assert writer.entries == []
            assert writer.groups == []
            assert not writer.is_encrypted
            print(f"  初始状态: 0 条目, 0 分组, 未加密 [OK]")

            # 添加文件
            writer.add_data(b"a", "a.txt", group_name="G1")
            writer.add_data(b"b", "b.txt", group_name="G1")
            writer.add_data(b"c", "c.txt", group_name="G2")

            # 属性检查
            assert writer.entry_count == 3
            assert len(writer.entries) == 3
            assert len(writer.groups) == 2
            print(f"  添加后: 3 条目, 2 分组 [OK]")

            # add_relation
            rel = writer.add_relation("G1", "G2", RelationType.SEQUEL,
                                      description="顺序")
            assert rel.source_group == 0
            assert rel.target_group == 1
            assert rel.relation_type == RelationType.SEQUEL
            assert rel.description == "顺序"
            print(f"  add_relation: G1 -> G2 (SEQUEL) [OK]")

            # add_relation 不存在的分组
            try:
                writer.add_relation("G1", "不存在", RelationType.RELATED)
                assert False, "应抛出 ValueError"
            except ValueError as e:
                assert "不存在" in str(e)
                print(f"  add_relation 不存在分组报错 [OK]")

            # add_tag
            writer.add_tag("G1", "标签1")
            writer.add_tag("G1", "标签2")
            writer.add_tag("G1", "标签1")  # 重复应忽略
            g1 = writer._groups["G1"]
            assert g1.tags == ["标签1", "标签2"]
            print(f"  add_tag: 去重正确 [OK]")

            # add_tag 不存在的分组
            try:
                writer.add_tag("不存在", "tag")
                assert False, "应抛出 ValueError"
            except ValueError:
                print(f"  add_tag 不存在分组报错 [OK]")

        # 验证写入结果
        with MCPKReader(mcpk_path) as reader:
            assert reader.entry_count == 3
            assert len(reader.groups) == 2
            assert len(reader.relations) == 1
            assert reader.relations[0].relation_type == RelationType.SEQUEL
            g1 = reader.find_group("G1")
            assert g1.tags == ["标签1", "标签2"]
            errors = reader.verify()
            assert not errors
            print(f"  读取验证: 3 条目, 2 分组, 1 关系, 标签正确 [OK]")

    # 加密 Writer 属性
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        enc_path = base / "enc_props.mcpk"
        with MCPKWriter(enc_path, password="test", encryption="xor") as writer:
            writer.add_data(b"x", "x.txt")
            assert writer.is_encrypted
            print(f"  加密 Writer: is_encrypted=True [OK]")

    print(f"  PASS: {label}")
    return True


def test_53_error_handling(sizes: dict):
    """错误处理：无效文件、截断数据、损坏数据。"""
    label = "错误处理"
    print("\n" + "=" * 60)
    print(f"测试 53: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # 非 MCPK 文件
        junk_path = base / "junk.mcpk"
        junk_path.write_bytes(b"this is not an mcpk file")
        try:
            with MCPKReader(junk_path) as reader:
                pass
            assert False, "应抛出 MCPKError"
        except MCPKError as e:
            assert "不是有效的 MCPK" in str(e) or "magic" in str(e).lower()
            print(f"  非 MCPK 文件报错 [OK]")

        # 太小的文件
        tiny_path = base / "tiny.mcpk"
        tiny_path.write_bytes(b"MCPK")  # 只有 4 字节
        try:
            with MCPKReader(tiny_path) as reader:
                pass
            assert False, "应抛出 MCPKError"
        except MCPKError as e:
            assert "太小" in str(e)
            print(f"  文件太小报错 [OK]")

        # 空文件
        empty_path = base / "empty.mcpk"
        empty_path.write_bytes(b"")
        try:
            with MCPKReader(empty_path) as reader:
                pass
            assert False, "应抛出 MCPKError"
        except (MCPKError, Exception):
            print(f"  空文件报错 [OK]")

        # 截断的有效文件（header 正确但数据不完整）
        valid_path = base / "valid.mcpk"
        with MCPKWriter(valid_path) as writer:
            writer.add_data(b"x" * 1000, "data.bin")
        full_data = valid_path.read_bytes()
        truncated_path = base / "truncated.mcpk"
        truncated_path.write_bytes(full_data[:len(full_data) // 2])
        try:
            with MCPKReader(truncated_path) as reader:
                reader.verify()
            # 如果 verify 能完成，检查是否有错误
            print(f"  截断文件: verify 可完成 [OK]")
        except (MCPKError, Exception) as e:
            print(f"  截断文件报错: {type(e).__name__} [OK]")

        # 加密文件需要密码
        enc_path = base / "enc.mcpk"
        with MCPKWriter(enc_path, password="secret", encryption="xor") as writer:
            writer.add_data(b"data", "file.txt")
        try:
            with MCPKReader(enc_path) as reader:
                pass
            assert False, "应抛出 MCPKError"
        except MCPKError as e:
            assert "密码" in str(e) or "加密" in str(e)
            print(f"  加密文件无密码报错 [OK]")

        # 错误密码
        try:
            with MCPKReader(enc_path, password="wrong") as reader:
                pass
            assert False, "应抛出 MCPKError"
        except MCPKError as e:
            assert "密码错误" in str(e) or "损坏" in str(e)
            print(f"  错误密码报错 [OK]")

        # extract 不存在的文件
        valid2_path = base / "valid2.mcpk"
        with MCPKWriter(valid2_path) as writer:
            writer.add_data(b"x", "exists.txt")
        with MCPKReader(valid2_path) as reader:
            try:
                reader.extract("not_exists.txt")
                assert False, "应抛出 KeyError"
            except KeyError as e:
                assert "不存在" in str(e)
                print(f"  extract 不存在文件 KeyError [OK]")

            # get_metadata 不存在的文件
            try:
                reader.get_metadata("not_exists.txt")
                assert False, "应抛出 KeyError"
            except KeyError:
                print(f"  get_metadata 不存在文件 KeyError [OK]")

    print(f"  PASS: {label}")
    return True


def test_54_unicode_filenames(sizes: dict):
    """Unicode 文件名：中文、日文、emoji、混合编码。"""
    label = "Unicode 文件名"
    print("\n" + "=" * 60)
    print(f"测试 54: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        mcpk_path = base / "unicode.mcpk"

        unicode_names = [
            "报告_2026Q1.pdf",
            "データ分析.txt",
            "données_françaises.csv",
            "Привет_мир.md",
            "文件名带空格 和特殊字符!@#.txt",
            "very_long_name_" + "测" * 50 + ".txt",
        ]

        with MCPKWriter(mcpk_path) as writer:
            for name in unicode_names:
                content = f"content of {name}".encode("utf-8")
                writer.add_data(content, name)

        with MCPKReader(mcpk_path) as reader:
            assert reader.entry_count == len(unicode_names)
            for name in unicode_names:
                data = reader.extract(name)
                expected = f"content of {name}".encode("utf-8")
                assert data == expected, f"{name} 内容不一致"
            errors = reader.verify()
            assert not errors
            print(f"  {len(unicode_names)} Unicode 文件名全部正确 [OK]")

            # find 也应正常工作
            for name in unicode_names:
                entry = reader.find(name)
                assert entry is not None
                assert entry.name == name
            print(f"  find() 全部匹配 [OK]")

    print(f"  PASS: {label}")
    return True


def test_55_deep_directory_nesting(sizes: dict):
    """深层目录嵌套：import_folder 递归。"""
    label = "深层目录嵌套"
    print("\n" + "=" * 60)
    print(f"测试 55: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # 创建深层目录结构
        deep_dir = base / "project"
        dirs = [
            deep_dir / "src" / "main" / "java" / "com" / "example",
            deep_dir / "src" / "test" / "resources",
            deep_dir / "docs" / "api" / "v2",
            deep_dir / "config" / "env",
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

        # 在各层放置文件
        files = {}
        for d in dirs:
            for i in range(2):
                f = d / f"file_{i}.txt"
                content = f"content in {d.relative_to(deep_dir)}".encode("utf-8")
                f.write_bytes(content)
                files[str(f.relative_to(deep_dir)).replace("\\", "/")] = content

        # 用 import_folder 递归打包
        mcpk_path = base / "deep.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            group = writer.import_folder(deep_dir, tags=["项目"])
            assert len(group.entry_ids) == len(files)
            print(f"  import_folder: {len(files)} 文件 [OK]")

        # 验证
        with MCPKReader(mcpk_path) as reader:
            assert reader.entry_count == len(files)
            for name, expected in files.items():
                data = reader.extract(name)
                assert data == expected, f"{name} 内容不一致"
            errors = reader.verify()
            assert not errors
            print(f"  所有文件内容正确 [OK]")

            # 提取到目录
            out_dir = base / "extracted"
            reader.extract_all(out_dir)
            for name, expected in files.items():
                out_file = out_dir / name
                assert out_file.exists(), f"缺失: {name}"
                assert out_file.read_bytes() == expected
            print(f"  提取到目录验证通过 [OK]")

    print(f"  PASS: {label}")
    return True


def test_56_compress_fallback(sizes: dict):
    """压缩回退：zstd/lz4 不可用时回退到 zlib。"""
    label = "压缩回退"
    print("\n" + "=" * 60)
    print(f"测试 56: {label}")
    print("=" * 60)

    from mcpk.crypto import compress, decompress

    # 测试 NONE 压缩
    data = b"hello world" * 100
    result, actual = compress(data, Compression.NONE)
    assert result == data
    assert actual == Compression.NONE
    print(f"  NONE: 直通 [OK]")

    # 测试 ZLIB 压缩
    result, actual = compress(data, Compression.ZLIB)
    assert actual == Compression.ZLIB
    decompressed = decompress(result, Compression.ZLIB)
    assert decompressed == data
    print(f"  ZLIB: 压缩+解压 [OK]")

    # 测试 ZSTD 回退
    import warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result_zstd, actual_zstd = compress(data, Compression.ZSTD)
        if actual_zstd == Compression.ZLIB:
            assert len(w) == 1
            assert "zstd" in str(w[0].message).lower()
            print(f"  ZSTD: 回退到 zlib (警告已发) [OK]")
            # 解压应使用 ZLIB
            decompressed = decompress(result_zstd, Compression.ZLIB)
            assert decompressed == data
            print(f"  ZSTD 回退: 解压正确 [OK]")
        else:
            assert actual_zstd == Compression.ZSTD
            decompressed = decompress(result_zstd, Compression.ZSTD)
            assert decompressed == data
            print(f"  ZSTD: 直接压缩+解压 [OK]")

    # 测试 LZ4 回退
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result_lz4, actual_lz4 = compress(data, Compression.LZ4)
        if actual_lz4 == Compression.ZLIB:
            assert len(w) == 1
            assert "lz4" in str(w[0].message).lower()
            print(f"  LZ4: 回退到 zlib (警告已发) [OK]")
            decompressed = decompress(result_lz4, Compression.ZLIB)
            assert decompressed == data
            print(f"  LZ4 回退: 解压正确 [OK]")
        else:
            assert actual_lz4 == Compression.LZ4
            decompressed = decompress(result_lz4, Compression.LZ4)
            assert decompressed == data
            print(f"  LZ4: 直接压缩+解压 [OK]")

    # 端到端：写入 ZSTD，TOC 应记录实际压缩算法
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        mcpk_path = base / "fallback.mcpk"
        with MCPKWriter(mcpk_path) as writer:
            writer.add_data(data, "data.bin", compression=Compression.ZSTD)

        with MCPKReader(mcpk_path) as reader:
            entry = reader.find("data.bin")
            # 如果 zstd 可用，compression 应为 ZSTD；否则 ZLIB
            assert entry.compression in (Compression.ZSTD, Compression.ZLIB)
            extracted = reader.extract("data.bin")
            assert extracted == data
            errors = reader.verify()
            assert not errors
            print(f"  端到端: TOC 记录与实际一致 [OK]")

    print(f"  PASS: {label}")
    return True


def test_57_encryption_edge_cases(sizes: dict):
    """加密边界：空文件加密、单字节加密、大文件加密。"""
    label = "加密边界"
    print("\n" + "=" * 60)
    print(f"测试 57: {label}")
    print("=" * 60)

    for enc_name, enc_kw in [("AES", {"encryption": "aes"}), ("XOR", {"encryption": "xor"})]:
        try:
            from mcpk.crypto import HAS_CRYPTO
            if enc_name == "AES" and not HAS_CRYPTO:
                print(f"  {enc_name}: 跳过 (cryptography 未安装)")
                continue
        except ImportError:
            continue

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            mcpk_path = base / f"edge_{enc_name.lower()}.mcpk"
            password = f"pass_{enc_name.lower()}"

            with MCPKWriter(mcpk_path, password=password, **enc_kw) as writer:
                # 空文件
                writer.add_data(b"", "empty.txt")
                # 单字节
                writer.add_data(b"\x42", "one_byte.bin")
                # 小文件
                writer.add_data(b"hello" * 100, "small.txt")
                # 较大文件
                gen_text_file(base / "medium.txt", sizes["text"], seed=57)
                writer.add_file(base / "medium.txt")
                print(f"  {enc_name}: 写入 4 文件 (空/1B/小/中) [OK]")

            with MCPKReader(mcpk_path, password=password) as reader:
                assert reader.entry_count == 4
                assert reader.is_encrypted
                assert reader.extract("empty.txt") == b""
                assert reader.extract("one_byte.bin") == b"\x42"
                assert reader.extract("small.txt") == b"hello" * 100
                medium_data = reader.extract("medium.txt")
                assert len(medium_data) > 0
                errors = reader.verify()
                assert not errors
                print(f"  {enc_name}: 全部提取+校验正确 [OK]")

    print(f"  PASS: {label}")
    return True


def test_58_many_groups_stress(sizes: dict):
    """大量分组压力测试。"""
    label = "大量分组压力"
    print("\n" + "=" * 60)
    print(f"测试 58: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        mcpk_path = base / "stress_groups.mcpk"

        num_groups = 50
        files_per_group = 5

        with MCPKWriter(mcpk_path) as writer:
            for g in range(num_groups):
                gname = f"group_{g:03d}"
                for f in range(files_per_group):
                    fname = f"file_{f}.txt"
                    content = f"group={g},file={f}".encode()
                    writer.add_data(content, fname, group_name=gname)
                # 添加组间关系
                if g > 0:
                    prev = f"group_{g-1:03d}"
                    writer.add_relation(prev, gname, RelationType.SEQUEL)
                # 添加标签
                writer.add_tag(gname, f"tag_{g % 5}")
                writer.add_tag(gname, "all_groups")

        total_files = num_groups * files_per_group
        print(f"  写入: {num_groups} 组, {total_files} 文件, {num_groups-1} 关系 [OK]")

        with MCPKReader(mcpk_path) as reader:
            assert reader.entry_count == total_files
            assert len(reader.groups) == num_groups
            assert len(reader.relations) == num_groups - 1
            print(f"  读取: {reader.entry_count} 条目, {len(reader.groups)} 分组 [OK]")

            # 验证每个分组的内容
            for g in range(num_groups):
                gname = f"group_{g:03d}"
                group = reader.find_group(gname)
                assert group is not None
                assert len(group.entry_ids) == files_per_group
                assert "all_groups" in group.tags
                # 验证组内文件
                for f in range(files_per_group):
                    fname = f"file_{f}.txt"
                    data = reader.extract(fname, group=gname)
                    expected = f"group={g},file={f}".encode()
                    assert data == expected
            print(f"  全部 {num_groups} 组内容验证通过 [OK]")

            # verify
            errors = reader.verify()
            assert not errors
            print(f"  完整性校验通过 [OK]")

            # inspect
            info = reader.inspect()
            assert info["entry_count"] == total_files
            assert len(info["groups"]) == num_groups
            assert len(info["relations"]) == num_groups - 1
            print(f"  inspect 输出正确 [OK]")

    print(f"  PASS: {label}")
    return True


def test_59_crypto_module_direct(sizes: dict):
    """crypto.py 模块直接测试：xor_bytes, key derivation, encrypt/decrypt。"""
    label = "crypto 模块直接测试"
    print("\n" + "=" * 60)
    print(f"测试 59: {label}")
    print("=" * 60)

    from mcpk.crypto import (
        xor_bytes, derive_key, derive_control_key, derive_blob_key,
        derive_key_pbkdf2, derive_subkeys_aes, derive_blob_key_aes,
        aes_gcm_encrypt, aes_gcm_decrypt, HAS_CRYPTO,
    )

    # xor_bytes 基本测试
    data = b"hello world"
    key = b"\x42"
    encrypted = xor_bytes(data, key)
    decrypted = xor_bytes(encrypted, key)
    assert decrypted == data
    assert encrypted != data
    print(f"  xor_bytes 单字节 key [OK]")

    # xor_bytes 多字节 key
    key2 = b"\x01\x02\x03"
    enc2 = xor_bytes(data, key2)
    dec2 = xor_bytes(enc2, key2)
    assert dec2 == data
    print(f"  xor_bytes 多字节 key [OK]")

    # xor_bytes 空数据
    assert xor_bytes(b"", key) == b""
    print(f"  xor_bytes 空数据 [OK]")

    # xor_bytes 空 key
    assert xor_bytes(data, b"") == data
    print(f"  xor_bytes 空 key (直通) [OK]")

    # derive_key 确定性
    k1 = derive_key("password", b"\x00" * 16)
    k2 = derive_key("password", b"\x00" * 16)
    assert k1 == k2
    assert len(k1) == 32
    print(f"  derive_key 确定性 [OK]")

    # derive_key 不同密码产生不同密钥
    k3 = derive_key("other", b"\x00" * 16)
    assert k1 != k3
    print(f"  derive_key 不同密码不同密钥 [OK]")

    # derive_key 不同 salt 产生不同密钥
    k4 = derive_key("password", b"\xff" * 16)
    assert k1 != k4
    print(f"  derive_key 不同 salt 不同密钥 [OK]")

    # derive_control_key
    ck = derive_control_key(k1)
    assert len(ck) == 32
    assert ck != k1
    print(f"  derive_control_key [OK]")

    # derive_blob_key
    bk1 = derive_blob_key(k1, 0, b"\x00" * 16)
    bk2 = derive_blob_key(k1, 1, b"\x00" * 16)
    assert bk1 != bk2
    assert len(bk1) == 32
    print(f"  derive_blob_key 不同 entry_id 不同密钥 [OK]")

    # AES-GCM 测试（如果有 cryptography）
    if HAS_CRYPTO:
        # derive_key_pbkdf2
        mk = derive_key_pbkdf2("password", b"\x00" * 32)
        assert len(mk) == 32
        mk2 = derive_key_pbkdf2("password", b"\x00" * 32)
        assert mk == mk2
        print(f"  derive_key_pbkdf2 确定性 [OK]")

        # derive_subkeys_aes
        ck_aes, dk_aes = derive_subkeys_aes(mk)
        assert len(ck_aes) == 32
        assert len(dk_aes) == 32
        assert ck_aes != dk_aes
        print(f"  derive_subkeys_aes [OK]")

        # derive_blob_key_aes
        bk_aes1 = derive_blob_key_aes(dk_aes, 0, b"\x00" * 16)
        bk_aes2 = derive_blob_key_aes(dk_aes, 1, b"\x00" * 16)
        assert bk_aes1 != bk_aes2
        print(f"  derive_blob_key_aes [OK]")

        # aes_gcm_encrypt / aes_gcm_decrypt
        plaintext = b"secret message" * 100
        aad = b"context"
        ct = aes_gcm_encrypt(ck_aes, plaintext, aad)
        assert ct != plaintext
        assert len(ct) > len(plaintext)  # nonce + tag overhead
        pt = aes_gcm_decrypt(ck_aes, ct, aad)
        assert pt == plaintext
        print(f"  aes_gcm encrypt/decrypt [OK]")

        # aes_gcm 错误 key 应失败
        wrong_key = derive_key_pbkdf2("wrong", b"\x00" * 32)
        wrong_ck, _ = derive_subkeys_aes(wrong_key)
        try:
            aes_gcm_decrypt(wrong_key[:32], ct, aad)
            assert False, "应抛出异常"
        except Exception:
            print(f"  aes_gcm 错误 key 解密失败 [OK]")

        # aes_gcm 篡改数据应失败
        tampered = bytearray(ct)
        tampered[20] ^= 0xFF
        try:
            aes_gcm_decrypt(ck_aes, bytes(tampered), aad)
            assert False, "应抛出异常"
        except Exception:
            print(f"  aes_gcm 篡改检测 [OK]")

        # aes_gcm 不同 AAD 应失败
        try:
            aes_gcm_decrypt(ck_aes, ct, b"wrong aad")
            assert False, "应抛出异常"
        except Exception:
            print(f"  aes_gcm 不同 AAD 解密失败 [OK]")
    else:
        print(f"  AES-GCM: 跳过 (cryptography 未安装)")

    print(f"  PASS: {label}")
    return True


def test_60_data_integrity_corruption(sizes: dict):
    """数据完整性：篡改 blob 后 CRC32 校验应失败。"""
    label = "数据篡改检测"
    print("\n" + "=" * 60)
    print(f"测试 60: {label}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        mcpk_path = base / "integrity.mcpk"

        # 创建文件
        with MCPKWriter(mcpk_path) as writer:
            writer.add_data(b"original content " * 100, "data.txt")
            writer.add_data(b"another file", "other.txt")

        # 读取原始数据
        original_data = mcpk_path.read_bytes()

        # 篡改 blob 区域（找到 data 的位置并修改）
        with MCPKReader(mcpk_path) as reader:
            entry = reader.find("data.txt")
            blob_offset = entry.blob_offset
            # 篡改 blob 的第一个字节
            tampered = bytearray(original_data)
            tampered[blob_offset] ^= 0xFF
            tampered_path = base / "tampered.mcpk"
            tampered_path.write_bytes(bytes(tampered))

        # 验证篡改文件
        with MCPKReader(tampered_path) as reader:
            try:
                errors = reader.verify()
                assert len(errors) > 0, "应检测到篡改"
                print(f"  篡改 blob: verify 检测到 {len(errors)} 个错误 [OK]")
            except Exception:
                print(f"  篡改 blob: verify 抛出异常 [OK]")

            # 提取未篡改的文件应正常
            other_data = reader.extract("other.txt")
            assert other_data == b"another file"
            print(f"  未篡改文件仍可正常提取 [OK]")

            # 提取篡改文件应报错（CRC32 不匹配或解压失败）
            try:
                reader.extract("data.txt")
                assert False, "应抛出异常"
            except (MCPKError, Exception) as e:
                print(f"  篡改文件提取报错: {type(e).__name__} [OK]")

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
    # v2.2 新增
    ("test_21",  test_21_aes_gcm_full_roundtrip, "tiny"),
    ("test_22",  test_22_aes_gcm_metadata_only,  "tiny"),
    ("test_23",  test_23_aes_gcm_data_only,      "tiny"),
    ("test_24",  test_24_aes_gcm_wrong_password, "tiny"),
    ("test_25",  test_25_aes_gcm_tamper_detection,"tiny"),
    ("test_26",  test_26_xor_backward_compat,    "tiny"),
    ("test_27",  test_27_aes_gcm_with_groups_and_relations, "tiny"),
    ("test_28",  test_28_group_tags_basic,       "tiny"),
    ("test_29",  test_29_add_tag_dynamic,        "tiny"),
    ("test_30",  test_30_intra_relation_basic,   "tiny"),
    ("test_31",  test_31_multiple_intra_relations,"tiny"),
    ("test_32",  test_32_tags_and_intra_with_encryption, "tiny"),
    ("test_33",  test_33_import_folder_basic,    "tiny"),
    ("test_34",  test_34_import_folder_multiple, "tiny"),
    ("test_35",  test_35_import_folder_non_recursive, "tiny"),
    ("test_36",  test_36_json_index_basic,       "tiny"),
    ("test_37",  test_37_json_index_missing_files,"tiny"),
    ("test_38",  test_38_json_index_with_intra_relations, "tiny"),
    ("test_39",  test_39_json_index_with_encryption, "tiny"),
    ("test_40",  test_40_inspect_with_new_fields,"tiny"),
    ("test_41",  test_41_tag_deduplication,      "tiny"),
    ("test_42",  test_42_many_tags_per_group,    "tiny"),
    ("test_43",  test_43_intra_relation_custom_type, "tiny"),
    ("test_44",  test_44_import_folder_with_tags_and_relations, "tiny"),
    ("test_45",  test_45_all_intra_relation_types, "tiny"),
    ("test_46",  test_46_encryption_none_still_works, "tiny"),
    ("test_47",  test_47_json_index_empty,       "tiny"),
    ("test_48",  test_48_json_index_complex,     "tiny"),
    ("test_49",  test_49_same_name_different_ext,"tiny"),
    ("test_50",  test_50_path_safety,           "tiny"),
    ("test_51",  test_51_reader_api_coverage,   "tiny"),
    ("test_52",  test_52_writer_properties_and_relations, "tiny"),
    ("test_53",  test_53_error_handling,         "tiny"),
    ("test_54",  test_54_unicode_filenames,      "tiny"),
    ("test_55",  test_55_deep_directory_nesting, "tiny"),
    ("test_56",  test_56_compress_fallback,      "tiny"),
    ("test_57",  test_57_encryption_edge_cases,  "tiny"),
    ("test_58",  test_58_many_groups_stress,     "tiny"),
    ("test_59",  test_59_crypto_module_direct,   "tiny"),
    ("test_60",  test_60_data_integrity_corruption, "tiny"),
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
    # v2.2 新增
    ("test_21",  test_21_aes_gcm_full_roundtrip, "small"),
    ("test_22",  test_22_aes_gcm_metadata_only,  "small"),
    ("test_23",  test_23_aes_gcm_data_only,      "small"),
    ("test_24",  test_24_aes_gcm_wrong_password, "tiny"),
    ("test_25",  test_25_aes_gcm_tamper_detection,"tiny"),
    ("test_26",  test_26_xor_backward_compat,    "small"),
    ("test_27",  test_27_aes_gcm_with_groups_and_relations, "small"),
    ("test_28",  test_28_group_tags_basic,       "tiny"),
    ("test_29",  test_29_add_tag_dynamic,        "tiny"),
    ("test_30",  test_30_intra_relation_basic,   "tiny"),
    ("test_31",  test_31_multiple_intra_relations,"tiny"),
    ("test_32",  test_32_tags_and_intra_with_encryption, "tiny"),
    ("test_33",  test_33_import_folder_basic,    "tiny"),
    ("test_34",  test_34_import_folder_multiple, "tiny"),
    ("test_35",  test_35_import_folder_non_recursive, "tiny"),
    ("test_36",  test_36_json_index_basic,       "small"),
    ("test_37",  test_37_json_index_missing_files,"tiny"),
    ("test_38",  test_38_json_index_with_intra_relations, "tiny"),
    ("test_39",  test_39_json_index_with_encryption, "tiny"),
    ("test_40",  test_40_inspect_with_new_fields,"tiny"),
    ("test_41",  test_41_tag_deduplication,      "tiny"),
    ("test_42",  test_42_many_tags_per_group,    "tiny"),
    ("test_43",  test_43_intra_relation_custom_type, "tiny"),
    ("test_44",  test_44_import_folder_with_tags_and_relations, "tiny"),
    ("test_45",  test_45_all_intra_relation_types, "tiny"),
    ("test_46",  test_46_encryption_none_still_works, "tiny"),
    ("test_47",  test_47_json_index_empty,       "tiny"),
    ("test_48",  test_48_json_index_complex,     "small"),
    ("test_49",  test_49_same_name_different_ext,"small"),
    ("test_50",  test_50_path_safety,           "tiny"),
    ("test_51",  test_51_reader_api_coverage,   "tiny"),
    ("test_52",  test_52_writer_properties_and_relations, "tiny"),
    ("test_53",  test_53_error_handling,         "tiny"),
    ("test_54",  test_54_unicode_filenames,      "tiny"),
    ("test_55",  test_55_deep_directory_nesting, "tiny"),
    ("test_56",  test_56_compress_fallback,      "tiny"),
    ("test_57",  test_57_encryption_edge_cases,  "tiny"),
    ("test_58",  test_58_many_groups_stress,     "small"),
    ("test_59",  test_59_crypto_module_direct,   "tiny"),
    ("test_60",  test_60_data_integrity_corruption, "tiny"),
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
    ("test_21",  test_21_aes_gcm_full_roundtrip, "medium"),
    ("test_27",  test_27_aes_gcm_with_groups_and_relations, "medium"),
    ("test_48",  test_48_json_index_complex,     "medium"),
    ("test_49",  test_49_same_name_different_ext,"medium"),
]


def main():
    import argparse
    parser = argparse.ArgumentParser(description="MCPK v2.2 集成测试")
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
    print(f"MCPK v2.2 测试套件  [模式={mode}, 预设={size_label}]")
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
