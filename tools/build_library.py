# -*- coding: utf-8 -*-
"""无期迷途资源库构建：从官方 CDN 流式拉取全部 chunk，提取立绘与 Live2D 清单。

流式管线（峰值磁盘 = 单个 chunk + 其拆分物）：
    下载 chunk → 拆分 v8 块 → 逐块扫描：
        lihui_* Texture2D → extracted/static/<名>/painting.png（跨 chunk 保留最大）
        MOC3 所在 bundle   → 记入 live2d_inventory.json（模型装配的二次定位）
    处理完立即删除 chunk 与拆分物

断点：state.json 记录已处理 chunk；跳过重跑即续传。

用法：
    python tools/build_library.py              # 全量（63 chunk ≈ 17.8GB 下载）
    python tools/build_library.py --limit 2    # 只处理 2 个（调试）
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MD = ROOT / "resources" / "metadata"
EXTRACTED = ROOT / "resources" / "extracted"
STATIC_OUT = EXTRACTED / "static"
WORK = ROOT / "resources" / "bundles"          # chunk 下载落点（处理后清理）
SPLIT_TMP = ROOT / "resources" / "bundles" / "_split_tmp"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

LIHUI_RE = re.compile(r"^lihui_[A-Za-z0-9]+?_.+")


def load_state() -> dict:
    p = MD / "library_state.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"processed": [], "static_best": {}, "moc3_bundles": {}}


def save_state(st: dict) -> None:
    p = MD / "library_state.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def process_bundle(data: bytes, src_name: str, st: dict) -> None:
    import UnityPy
    try:
        env = UnityPy.load(data)
    except Exception:  # noqa: BLE001
        return
    for obj in env.objects:
        try:
            if obj.type.name == "Texture2D":
                tex = obj.read()
                name = tex.m_Name or ""
                if not LIHUI_RE.match(name):
                    continue
                size = (tex.m_Width, tex.m_Height)
                prev = st["static_best"].get(name)
                if prev and (size[0] * size[1]) <= (prev["w"] * prev["h"]):
                    continue
                img = tex.image
                dest = STATIC_OUT / name
                dest.mkdir(parents=True, exist_ok=True)
                img.save(dest / "painting.png")
                st["static_best"][name] = {"w": size[0], "h": size[1], "chunk": src_name}
            elif obj.type.name == "MonoBehaviour":
                raw_bytes = b""
                try:
                    raw_bytes = obj.get_raw_data()
                except Exception:  # noqa: BLE001
                    raw_bytes = b""
                if b"MOC3" not in raw_bytes[:64]:
                    # MonoBehaviour 头部含 GameObject/Script 引用，MOC3 位于 _bytes 字段中段
                    if b"MOC3" not in raw_bytes:
                        continue
                    raw_bytes = raw_bytes[raw_bytes.index(b"MOC3"):]
                mname = ""
                try:
                    mname = obj.read().m_Name or ""
                except Exception:  # noqa: BLE001
                    pass
                key = mname or f"moc3_{obj.path_id}"
                st["moc3_bundles"].setdefault(key, {"bundle": src_name, "size": len(raw_bytes)})
        except Exception:  # noqa: BLE001
            continue


def main() -> int:
    ap = argparse.ArgumentParser(description="流式构建无期迷途资源库")
    ap.add_argument("--limit", type=int, default=0, help="本次最多处理 N 个未处理 chunk")
    args = ap.parse_args()

    from plugins.sources.ptn_cdn import PtnCdnSource
    from tools.split_ptn_chunk import read_block

    src = PtnCdnSource()
    n = src.ensure_chunk_list()
    if not n:
        print("chunk 清单为空（resources/metadata/chunk_list.txt 缺失）")
        return 2
    print(f"chunk 清单: {n} 个")

    st = load_state()
    todo = [c.name for c in src.list_bundles() if c.name not in st["processed"]]
    print(f"待处理: {len(todo)}（已完成 {len(st['processed'])}）")
    if args.limit:
        todo = todo[: args.limit]

    WORK.mkdir(parents=True, exist_ok=True)
    import UnityPy  # noqa: F401  预热

    for idx, name in enumerate(todo, 1):
        t0 = time.time()
        print(f"[{idx}/{len(todo)}] {name} 下载中…", flush=True)
        try:
            bin_path = src.download_chunk(name, WORK)
        except Exception as e:  # noqa: BLE001
            print(f"  下载失败: {e}", flush=True)
            continue
        raw = bin_path.read_bytes()
        # 流式拆分：逐块读出，处理完丢弃
        off, bundles = 0, 0
        try:
            while off < len(raw):
                block = read_block(raw, off)
                if block is None:
                    break
                data, next_off = block
                bundles += 1
                process_bundle(data, name, st)
                off = next_off
        except ValueError as e:
            print(f"  拆分异常: {e}", flush=True)
        st["processed"].append(name)
        if idx % 3 == 0 or idx == len(todo):
            save_state(st)
        bin_path.unlink(missing_ok=True)
        shutil.rmtree(SPLIT_TMP, ignore_errors=True)
        print(f"  完成: {bundles} 块, 立绘 {len(st['static_best'])}, "
              f"Live2D 模型 {len(st['moc3_bundles'])}, {time.time()-t0:.0f}s", flush=True)

    save_state(st)
    # 产物清单落盘
    (MD / "live2d_inventory.json").write_text(
        json.dumps(st["moc3_bundles"], ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"全部完成: 立绘 {len(st['static_best'])} 个, Live2D 模型 {len(st['moc3_bundles'])} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
