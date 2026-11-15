# -*- coding: utf-8 -*-
"""压平已提取 Live2D 动作里「背景层比人物层长」造成的待机空档。

PTN 的动作常把人物层与背景层合并进同一条 motion3.json：人物曲线先结束
（例如待机 12.7s），背景花瓣/粒子按 21.6s 循环，而 Meta.Duration 取的是背景长度。
壁纸按 Duration 循环时，人物会先定住、过一会才重新开始——即「待机动画播着播着
人物停住」的现象。

本脚本以 canonical Background.motion3.json 的曲线键判定背景-owned 曲线，把超出
人物动作时长的背景曲线等比压缩（首末值不变，循环无接缝），并把 Meta.Duration
收紧到人物动作时长。幂等：第二次运行 changed=0。

用法：
    python tools/normalize_motion_tail.py                  # 全量处理
    python tools/normalize_motion_tail.py Hypatia_3        # 指定模型
    python tools/normalize_motion_tail.py Hypatia_3 --dry-run
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRACTED = ROOT / "resources" / "extracted" / "live2d"

sys.path.insert(0, str(ROOT / "tools"))
from motion3_utils import normalize_motion_tail  # noqa: E402

_BG_NAME_RE = re.compile(r"(?:^|_)background$", re.IGNORECASE)
_MOTION_SUFFIX = ".motion3.json"


def is_background_clip(name: str) -> bool:
    stem = name[: -len(_MOTION_SUFFIX)] if name.endswith(_MOTION_SUFFIX) else Path(name).stem
    return bool(_BG_NAME_RE.search(stem))


def canonical_background(motions_dir: Path) -> tuple[Path, dict] | None:
    """挑出真正的背景层动作：优先同名 Background，排除 Click_Background 之类。"""
    candidates: list[Path] = []
    for file in sorted(motions_dir.glob(f"*{_MOTION_SUFFIX}")):
        stem = file.name[: -len(_MOTION_SUFFIX)]
        if not _BG_NAME_RE.search(stem) or stem.casefold().startswith("click"):
            continue
        candidates.append(file)
    if not candidates:
        return None
    exact = [file for file in candidates if file.name[: -len(_MOTION_SUFFIX)].casefold() == "background"]
    chosen = exact[0] if exact else candidates[0]
    try:
        return chosen, json.loads(chosen.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def process_model(model_dir: Path, dry_run: bool = False) -> dict:
    stats = {"model": model_dir.name, "changed": 0, "unchanged": 0, "skipped": 0, "errors": 0}
    motions_dir = model_dir / "motions"
    if not motions_dir.is_dir():
        return stats
    background = canonical_background(motions_dir)
    if background is None:
        return stats
    background_file, background_motion = background
    background_keys = {
        (curve.get("Target"), curve.get("Id")) for curve in background_motion.get("Curves", [])
    }

    for file in sorted(motions_dir.glob(f"*{_MOTION_SUFFIX}")):
        if file == background_file or is_background_clip(file.name):
            # 背景层动作本身就是 canonical 循环，不做收紧。
            stats["skipped"] += 1
            continue
        try:
            motion = json.loads(file.read_text(encoding="utf-8"))
            if not normalize_motion_tail(motion, background_keys):
                stats["unchanged"] += 1
                continue
            if not dry_run:
                atomic_write_json(file, motion)
            stats["changed"] += 1
            duration = float(motion.get("Meta", {}).get("Duration", 0) or 0)
            print(f"  [tail] {model_dir.name}/{file.name}: duration={duration:.3f}")
        except ValueError as exc:
            stats["errors"] += 1
            print(f"  [malformed] {model_dir.name}/{file.name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            stats["errors"] += 1
            print(f"  [error] {model_dir.name}/{file.name}: {exc}")
    return stats


def main() -> int:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    targets = [arg for arg in args if not arg.startswith("--")]
    model_dirs = [EXTRACTED / target for target in targets] if targets else sorted(
        path for path in EXTRACTED.iterdir() if path.is_dir()
    )

    totals = {"models": 0, "changed": 0, "unchanged": 0, "skipped": 0, "errors": 0}
    for model_dir in model_dirs:
        totals["models"] += 1
        stats = process_model(model_dir, dry_run=dry_run)
        for key in ("changed", "unchanged", "skipped", "errors"):
            totals[key] += stats[key]
        if stats["changed"] or stats["errors"]:
            print(
                f"{stats['model']}: changed={stats['changed']} unchanged={stats['unchanged']} "
                f"skipped={stats['skipped']} errors={stats['errors']}"
            )
    mode = "（dry-run，未写盘）" if dry_run else ""
    print(f"完成: {totals} {mode}")
    return 0 if totals["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
