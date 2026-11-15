# -*- coding: utf-8 -*-
"""无期迷途 chunk 拆分器。

CDN 下载的 ``chunk/res_{md5}.bin``（游戏本地 fschunk/ 目录同名同内容）是
"块序列" 容器，每个块：

    [50B 前缀][50B UnityFS 头（逐字节 XOR，密钥可变）][payload]

- 密钥   = raw[off + 50] ^ 0x55，校验 raw[off + 51] ^ 0x6E 必须等于同一密钥
- 头内偏移 30 处的 int64（即 raw[off+80:off+88] 解密后）为 size 字段：
  该值表示 "整个 UnityFS 文件（头+负载）" 的长度 fs
- 下一个块起点 = off + 50 + fs
- 解密后的 raw[off+50 : off+50+fs] 即一个标准 UnityFS（Unity 2019.4.40f1），
  可直接交给 UnityPy / AssetStudio

块前的 50 字节前缀内容是块头的 XOR-0x26 镜像（元数据/占位），解析时跳过。

格式结论来自 2026-11 对 CDN 实样的静态分析，并与社区公开研究
（lylx1997/PathToNoWhere-Decrypt-and-Split）相互印证。

用法：
    python tools/split_ptn_chunk.py <chunk.bin|目录> [-o 输出目录] [--check]
默认输出到 <输入名>_split/；--check 只验证不解包。
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

HDR = b"UnityFS\x00"
_MAX_FS = 1024 * 1024 * 1024  # 单块上限 1GiB，防御异常 size


def _xor(data: bytes, key: int) -> bytes:
    table = bytes(i ^ key for i in range(256))
    return data.translate(table)


def read_block(raw: bytes, off: int) -> tuple[bytes, int] | None:
    """读取 off 处的一个块。

    返回 (解密后的完整 UnityFS 字节串, 下一块偏移)；无效返回 None。
    密钥校验失败视为文件损坏，抛 ValueError。
    """
    if off + 100 > len(raw):
        return None
    key = raw[off + 50] ^ 0x55
    if key != (raw[off + 51] ^ 0x6E):
        raise ValueError(f"偏移 {off}: XOR 密钥校验失败")
    header = _xor(raw[off + 50 : off + 100], key)
    if not header.startswith(HDR):
        return None
    fs = struct.unpack(">Q", header[30:38])[0]
    if fs < 128 or fs > _MAX_FS:
        raise ValueError(f"偏移 {off}: 异常块大小 {fs}")
    if off + 50 + fs > len(raw):
        raise ValueError(f"偏移 {off}: 块大小 {fs} 超出文件范围")
    body = _xor(raw[off + 50 : off + 50 + fs], key)
    return body, off + 50 + fs


def iter_blocks(chunk_path: Path, cancel_event=None):
    """从磁盘逐块解密 UnityFS，不把整个 CDN chunk 读入内存。"""
    size = chunk_path.stat().st_size
    off = 0
    with chunk_path.open("rb") as stream:
        while off < size:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("已取消")
            if size - off < 100:
                raise ValueError(
                    f"{chunk_path.name}: 尾部不足块头 ({size - off} 字节)"
                )
            stream.seek(off + 50)
            encrypted_header = stream.read(50)
            if len(encrypted_header) != 50:
                raise ValueError(f"{chunk_path.name}: 偏移 {off} 块头读取不完整")
            key = encrypted_header[0] ^ 0x55
            if key != (encrypted_header[1] ^ 0x6E):
                raise ValueError(f"偏移 {off}: XOR 密钥校验失败")
            header = _xor(encrypted_header, key)
            if not header.startswith(HDR):
                raise ValueError(f"偏移 {off}: 未找到 UnityFS 块头")
            fs = struct.unpack(">Q", header[30:38])[0]
            if fs < 128 or fs > _MAX_FS:
                raise ValueError(f"偏移 {off}: 异常块大小 {fs}")
            if off + 50 + fs > size:
                raise ValueError(f"偏移 {off}: 块大小 {fs} 超出文件范围")
            stream.seek(off + 50)
            encrypted = stream.read(fs)
            if len(encrypted) != fs:
                raise ValueError(f"偏移 {off}: 块读取不完整")
            next_off = off + 50 + fs
            yield _xor(encrypted, key), next_off
            off = next_off


def split_chunk(chunk_path: Path, out_dir: Path) -> list[Path]:
    """拆分单个 chunk，返回写出的 UnityFS 文件列表。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = chunk_path.stem
    written: list[Path] = []
    for index, (block, _next_off) in enumerate(iter_blocks(chunk_path), 1):
        dest = out_dir / f"{stem}_{index:03d}.unityfs"
        dest.write_bytes(block)
        written.append(dest)
    return written


def check_chunk(chunk_path: Path) -> tuple[int, int]:
    """只验证：返回 (块数, 覆盖字节数)。不写任何文件。"""
    count = 0
    covered = 0
    for count, (_block, covered) in enumerate(iter_blocks(chunk_path), 1):
        pass
    return count, covered


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="拆分无期迷途 chunk 容器")
    ap.add_argument("inputs", nargs="+", help="chunk .bin 文件或目录")
    ap.add_argument("-o", "--output", default=None, help="输出根目录")
    ap.add_argument("--check", action="store_true", help="只验证不落盘")
    args = ap.parse_args()

    files: list[Path] = []
    for item in args.inputs:
        p = Path(item)
        if p.is_dir():
            files.extend(sorted(p.glob("*.bin")))
        else:
            files.append(p)

    failures = 0
    for f in files:
        if args.check:
            count, covered = check_chunk(f)
            size = f.stat().st_size
            ok = covered == size
            status = "OK" if ok else f"异常: 覆盖 {covered}/{size}"
            print(f"{f.name}: {count} 块 {status}")
            failures += 0 if ok else 1
        else:
            out_root = Path(args.output) if args.output else f.parent / (f.stem + "_split")
            try:
                written = split_chunk(f, out_root)
            except ValueError as e:
                print(f"{f.name}: FAIL {e}")
                failures += 1
                continue
            print(f"{f.name}: 拆出 {len(written)} 个 UnityFS -> {out_root}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
