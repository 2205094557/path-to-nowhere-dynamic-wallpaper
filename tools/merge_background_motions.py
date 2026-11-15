# -*- coding: utf-8 -*-
"""为已提取 Live2D 动作合并并周期平铺 Background 曲线，修复合并待机的尾部静止。

人物动作常长于 Background 周期。仅复制一次背景曲线会使背景在关键帧末尾保持
静止；本脚本始终以独立 Background.motion3.json（或 *_background）为 canonical
周期，确定性重建人物动作中的背景-owned 曲线，直到覆盖人物动作 Duration。

同时处理「合并待机两层时长不一致」的缺陷：游戏内 Idle 层与 Background 层
独立循环，合并成单条动作后取 max 作为循环长度，较短的一层会在尾部保持
末值——表现为待机一会儿后人物定住（只剩呼吸/物理/视线在动），或一次性
动作结尾长时间静止。处理规则：
  - 待机动作（core/interaction.py 分类为 standby）：人物层按层周期平铺到
    总时长，只平铺首末闭环的曲线；
  - 一次性动作：Duration 裁到人物层末尾，人物停止即动作结束。

用法：
    python tools/merge_background_motions.py                 # 全量处理
    python tools/merge_background_motions.py Augustus_2      # 指定模型
    python tools/merge_background_motions.py Augustus_2 --dry-run

幂等：第二次运行应 changed=0。人物自有同 ID 曲线保留并计入 conflicts，不静默猜。
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
sys.path.insert(0, str(ROOT))
from core.interaction import STANDBY, classify_motion  # noqa: E402
from motion3_utils import (  # noqa: E402
    add_tiled_background_curves,
    recompute_motion_meta,
    tile_layer_curves,
    trim_motion_tail_to_layer,
)

_BG_NAME_RE = re.compile(r"(?:^|_)background$", re.IGNORECASE)


def is_background_clip(name: str) -> bool:
    stem = name[: -len(".motion3.json")] if name.endswith(".motion3.json") else Path(name).stem
    return bool(_BG_NAME_RE.search(stem))


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


def _semantic_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def process_model(model_dir: Path, dry_run: bool = False) -> dict:
    motions_dir = model_dir / "motions"
    stats = {
        "model": model_dir.name,
        "bg_clips": 0,
        "changed": 0,
        "unchanged": 0,
        "conflicts": 0,
        "held_curves": 0,
        "malformed": 0,
        "errors": 0,
    }
    if not motions_dir.is_dir():
        return stats

    backgrounds: list[tuple[Path, dict]] = []
    for file in sorted(motions_dir.glob("*.motion3.json")):
        if not is_background_clip(file.name):
            continue
        try:
            backgrounds.append((file, json.loads(file.read_text(encoding="utf-8"))))
        except Exception as exc:  # noqa: BLE001
            stats["errors"] += 1
            print(f"  [error] {model_dir.name}/{file.name}: {exc}")
    if not backgrounds:
        return stats
    stats["bg_clips"] = len(backgrounds)
    background_keys = {
        (curve.get("Target"), curve.get("Id"))
        for _, background in backgrounds
        for curve in background.get("Curves", [])
    }

    for file in sorted(motions_dir.glob("*.motion3.json")):
        try:
            motion = json.loads(file.read_text(encoding="utf-8"))
            before = _semantic_json(motion)
            conflicts: list[tuple] = []
            held: list[tuple] = []
            if not is_background_clip(file.name):
                # 一次性动作先裁掉尾部静止（裁短后背景曲线按新时长平铺）；
                # 待机动作的人物层按层周期平铺，消除「人物先停、等背景播完」。
                standby = classify_motion(file.name[: -len(".motion3.json")]) == STANDBY
                if not standby:
                    trim_motion_tail_to_layer(motion, background_keys)
                for _, background in backgrounds:
                    add_tiled_background_curves(motion, background, conflicts)
                if standby:
                    tile_layer_curves(motion, background_keys, skipped=held)
            # Background canonical 源与无背景动作同样修正历史错误 metadata。
            recompute_motion_meta(motion)
            after = _semantic_json(motion)
            stats["conflicts"] += len(conflicts)
            stats["held_curves"] += len(held)
            if before == after:
                stats["unchanged"] += 1
                continue
            if not dry_run:
                atomic_write_json(file, motion)
            stats["changed"] += 1
            duration = float(motion.get("Meta", {}).get("Duration", 0) or 0)
            suffix = f" conflicts={len(conflicts)}" if conflicts else ""
            if held:
                suffix += f" held={len(held)}"
            print(f"  [tile] {model_dir.name}/{file.name}: duration={duration:.3f}{suffix}")
        except ValueError as exc:
            stats["malformed"] += 1
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

    totals = {key: 0 for key in ("models", "with_bg", "changed", "unchanged", "conflicts", "held_curves", "malformed", "errors")}
    for model_dir in model_dirs:
        totals["models"] += 1
        stats = process_model(model_dir, dry_run=dry_run)
        if stats["bg_clips"]:
            totals["with_bg"] += 1
        for key in ("changed", "unchanged", "conflicts", "held_curves", "malformed", "errors"):
            totals[key] += stats[key]
        if stats["bg_clips"] or stats["errors"] or stats["malformed"]:
            print(
                f"{stats['model']}: bg={stats['bg_clips']} changed={stats['changed']} "
                f"unchanged={stats['unchanged']} conflicts={stats['conflicts']} "
                f"malformed={stats['malformed']} errors={stats['errors']}"
            )
    mode = "（dry-run，未写盘）" if dry_run else ""
    print(f"\n完成: {totals} {mode}")
    return 1 if totals["errors"] or totals["malformed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
