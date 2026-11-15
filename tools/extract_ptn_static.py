# -*- coding: utf-8 -*-
"""从拆分后的 UnityFS 批量提取静态立绘（lihui_* Texture2D）。

产物布局（与 build_local_index.py 约定一致）：
    resources/extracted/static/<lihui资源名>/painting.png

规则：
- 只导出 m_Name 以 lihui 开头的 Texture2D（完整图集；Sprite 是其裁切件，跳过）
- 同名资源出现在多个 bundle 时保留分辨率最大的一张
- 提取报告写 extracted/static/_report.json（数量/分辨率/来源 bundle）

用法：
    python tools/extract_ptn_static.py <拆分目录1> [目录2 ...] [--limit N]
开发期可直接指向研究目录的 split 输出；生产路径为 resources/bundles 拆分产物。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTRACTED = ROOT / "resources" / "extracted"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def main() -> int:
    ap = argparse.ArgumentParser(description="批量提取 lihui 静态立绘")
    ap.add_argument("sources", nargs="+", help="含 *.unityfs 的目录")
    ap.add_argument("--limit", type=int, default=0, help="最多导出 N 个名字（调试用）")
    args = ap.parse_args()

    import UnityPy

    out_root = EXTRACTED / "static"
    out_root.mkdir(parents=True, exist_ok=True)

    bundles: list[Path] = []
    for s in args.sources:
        p = Path(s)
        bundles.extend(sorted(p.rglob("*.unityfs")))
    print(f"待扫描 bundle: {len(bundles)}")

    best: dict[str, tuple[tuple[int, int], Path, bytes]] = {}
    t0 = time.time()
    scanned = 0
    for idx, bf in enumerate(bundles, 1):
        try:
            env = UnityPy.load(str(bf))
        except Exception:  # noqa: BLE001
            continue
        scanned += 1
        for obj in env.objects:
            if obj.type.name != "Texture2D":
                continue
            try:
                tex = obj.read()
                name = tex.m_Name or ""
            except Exception:  # noqa: BLE001
                continue
            if not name.startswith("lihui"):
                continue
            if not re.match(r"^lihui_[A-Za-z0-9]+?_.+", name):
                continue
            size = (tex.m_Width, tex.m_Height)
            prev = best.get(name)
            if prev is None or size > prev[0]:
                try:
                    img = tex.image
                except Exception:  # noqa: BLE001
                    continue
                best[name] = (size, bf, img.tobytes() if False else b"")
                # 直接保存，避免把 PIL 对象留在内存里
                dest = out_root / name
                dest.mkdir(parents=True, exist_ok=True)
                img.save(dest / "painting.png")
                best[name] = (size, bf, b"")
        if idx % 200 == 0:
            print(f"  [{idx}/{len(bundles)}] 已收录 {len(best)} 名, {time.time()-t0:.0f}s", flush=True)
        if args.limit and len(best) >= args.limit:
            break

    report = {
        "bundles_total": len(bundles),
        "bundles_scanned": scanned,
        "names_exported": len(best),
        "seconds": round(time.time() - t0, 1),
        "items": {
            name: {"w": s[0][0], "h": s[0][1], "bundle": s[1].name}
            for name, s in sorted(best.items())
        },
    }
    (out_root / "_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"完成: {len(best)} 个立绘 -> {out_root} ({report['seconds']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
