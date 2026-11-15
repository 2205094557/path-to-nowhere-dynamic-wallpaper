# -*- coding: utf-8 -*-
"""把 30Hz 阶梯关键帧（stepped）动作平滑成线性插值。

背景：部分模型的 motion3.json 是「全 stepped」的 30Hz 采样（实测 Uni_3 / Male_7 /
Hella_3 / Xiayin_2 / Tuoqier_3 / Etti_2）。stepped 的含义是「到下一个关键帧前
数值不变」，于是 60fps 渲染下每秒只有 30 次姿势更新，看起来就是 30fps 卡顿；
而同一角色其它装束用的是 bezier 曲线，观感顺滑，对比后更明显。

这些数值本身是连续运动在 30Hz 的采样点，把 segment 类型 2（stepped）改成 0（linear）
后运行时会逐帧插值，视觉上恢复顺滑。只改类型标记，不增删关键帧、不改时间与数值；
bezier(1)、linear(0)、inverse stepped(3) 一律原样保留。

用法：
    python tools/smooth_stepped_motions.py                 # 只扫描并输出报告
    python tools/smooth_stepped_motions.py --write         # 全部模型，按阈值处理
    python tools/smooth_stepped_motions.py Uni_3 Male_7 --write
    python tools/smooth_stepped_motions.py Uni_3 --ratio 0.99 --write
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from motion3_utils import (  # noqa: E402
    parse_curve_segments,
    recompute_motion_meta,
    serialize_curve_segments,
)

ROOT = Path(__file__).resolve().parents[1]
LIVE2D_DIR = ROOT / "resources" / "extracted" / "live2d"
BACKUP_DIR = ROOT / "resources" / "metadata" / "motion_originals"

STEPPED = 2
LINEAR = 0


def analyze_motion(motion: dict) -> dict[int, int]:
    """统计各类 segment 数量。"""
    counts: dict[int, int] = {}
    for curve in motion.get("Curves", []):
        _, segments = parse_curve_segments(curve.get("Segments", []))
        for segment in segments:
            kind = int(segment["type"])
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def smooth_motion(motion: dict, min_ratio: float) -> tuple[dict, int, int]:
    """把满足比例的 stepped segment 改成 linear，返回 (motion, 改动数, stepped 总数)。"""
    total_stepped = 0
    total_segments = 0
    for curve in motion.get("Curves", []):
        _, segments = parse_curve_segments(curve.get("Segments", []))
        total_segments += len(segments)
        total_stepped += sum(1 for s in segments if int(s["type"]) == STEPPED)
    if not total_segments:
        return motion, 0, 0
    ratio = total_stepped / total_segments
    if ratio < min_ratio:
        return motion, 0, total_stepped

    changed = 0
    for curve in motion.get("Curves", []):
        initial, segments = parse_curve_segments(curve.get("Segments", []))
        for segment in segments:
            if int(segment["type"]) == STEPPED:
                segment["type"] = LINEAR
                changed += 1
        curve["Segments"] = serialize_curve_segments(initial, segments)
    recompute_motion_meta(motion)
    return motion, changed, total_stepped


def _write_json(path: Path, payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _backup(path: Path, model: str) -> None:
    dest = BACKUP_DIR / model / path.name
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)


def iter_models(names: list[str]):
    if names:
        for name in names:
            path = LIVE2D_DIR / name
            if not path.is_dir():
                raise SystemExit(f"模型目录不存在: {path}")
            yield path
        return
    for path in sorted(LIVE2D_DIR.iterdir()):
        if path.is_dir():
            yield path


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="把 30Hz stepped 动作平滑为线性插值")
    ap.add_argument("models", nargs="*", help="模型目录名（默认全部）")
    ap.add_argument("--write", action="store_true", help="真正写回文件（默认只报告）")
    ap.add_argument("--ratio", type=float, default=0.9,
                    help="按单个动作文件判断：stepped 占比达到该值才平滑（默认 0.9）")
    args = ap.parse_args()

    scanned = processed = 0
    changed_files = 0
    for model_dir in iter_models(args.models):
        for motion_path in sorted(model_dir.rglob("*.motion3.json")):
            scanned += 1
            try:
                payload = json.loads(motion_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                print(f"[skip] 解析失败 {motion_path}: {exc}")
                continue
            counts = analyze_motion(payload)
            total = sum(counts.values())
            if not total:
                continue
            stepped = counts.get(STEPPED, 0)
            ratio = stepped / total
            label = f"{model_dir.name}/{motion_path.relative_to(model_dir).as_posix()}"
            if ratio < args.ratio:
                print(f"[keep] {label}: stepped {ratio:.1%}（{stepped}/{total}）")
                continue
            processed += 1
            print(f"[fix ] {label}: stepped {ratio:.1%}（{stepped}/{total}）")
            if not args.write:
                continue
            _backup(motion_path, model_dir.name)
            smoothed, changed, _ = smooth_motion(payload, args.ratio)
            _write_json(motion_path, smoothed)
            changed_files += 1
            print(f"       已改 {changed} 个 segment → linear（原文件备份于 "
                  f"{BACKUP_DIR.relative_to(ROOT)}/{model_dir.name}/{motion_path.name}）")

    if args.write:
        print(f"完成：扫描 {scanned} 个动作文件，改写 {changed_files} 个（命中 {processed} 个）")
    else:
        print(f"预演：扫描 {scanned} 个动作文件，命中 {processed} 个；加 --write 才会写回")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
