# -*- coding: utf-8 -*-
"""装配无期迷途 Live2D 模型（可由后端进程内安全调用）。"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zlib
from pathlib import Path

# sssekai.fmt.motion3 依赖本项目 tools/motion3_utils.py（背景曲线平铺/序列化），
# 确保脚本目录在 sys.path 中，使后台服务与命令行两种入口都能导入。
_TOOLS_DIR = str(Path(__file__).resolve().parent)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MD = ROOT / "resources" / "metadata"
WORK = ROOT / "resources" / "bundles"
OUT_ROOT = ROOT / "resources" / "extracted" / "live2d"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def find_model_entry(model: str, state_path: Path | None = None) -> dict:
    paths = [state_path] if state_path else [MD / "library_state.json", MD / "live2d_inventory.json"]
    for path in paths:
        if path and path.exists():
            inv = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(inv, dict) and "moc3_bundles" in inv:
                inv = inv["moc3_bundles"]
            if isinstance(inv, dict) and model in inv:
                return inv[model]
    raise RuntimeError(f"模型 {model} 不在 Live2D 资源索引中")


def _cancel(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("已取消")


def _asset_bundle_name(env) -> str:
    for obj in env.objects:
        if obj.type.name != "AssetBundle":
            continue
        try:
            return obj.read().m_Name or ""
        except Exception:  # noqa: BLE001
            continue
    return ""


def _family_member(ab_name: str) -> tuple[str, str] | None:
    if not ab_name.startswith("assets/art_live2d_"):
        return None
    stem = ab_name[len("assets/art_live2d_"):]
    suffixes = (
        ("_motion.bundle", "motion"),
        ("_animation.bundle", "animation"),
        ("_material.bundle", "material"),
        ("_effect.bundle", "effect"),
    )
    for suffix, kind in suffixes:
        if stem.endswith(suffix):
            return stem[:-len(suffix)], kind
    for resolution in (512, 1024, 2048, 4096):
        suffix = f".{resolution}.bundle"
        if stem.endswith(suffix):
            return stem[:-len(suffix)], "tex"
    if stem.endswith(".bundle"):
        return stem[:-len(".bundle")], "moc"
    return None


def _contains_model_moc(env, model: str) -> bool:
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            raw = obj.get_raw_data()
            if b"MOC3" not in raw[:80] and b"MOC3" not in raw:
                continue
            if (obj.read().m_Name or "").casefold() == model.casefold():
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def collect_model_family(
    blocks: Iterable[tuple[bytes, int]],
    model: str,
    *,
    cancel_event=None,
    emit: Callable[[str, str], None] | None = None,
    allow_stub_moc: bool = False,
) -> tuple[str, dict[str, bytes]]:
    """扫描 chunk 流，仅保留目标模型家族所需 bundle。

    两遍扫描避免把 chunk 内所有 Live2D 家族常驻内存：第一遍只定位目标
    MOC 家族，第二遍只保留该家族的贴图/动作/配置 bundle。

    allow_stub_moc：classify 通道的 moc bundle 是 PTN 特殊容器（MOC3 数据在
    UnityPy 可读范围之外），MonoBehaviour raw 里没有 MOC3 魔数，此时按
    AssetBundle 名匹配家族即可认定 moc，moc3 字节由调用方提供。
    """
    import UnityPy

    target_prefix: str | None = None
    target_moc: bytes | None = None
    block_source = blocks if callable(blocks) else None
    if block_source is None:
        # 一般调用方传 iter_blocks 生成器；为允许第二遍扫描，仅在测试等小输入下缓存。
        cached = list(blocks)
        block_source = lambda: iter(cached)

    for index, (data, _next_off) in enumerate(block_source(), 1):
        _cancel(cancel_event)
        if emit:
            emit("正在定位 Live2D 模型", f"第 {index} 块")
        try:
            env = UnityPy.load(data)
        except Exception:  # noqa: BLE001
            continue
        member = _family_member(_asset_bundle_name(env))
        if not member:
            continue
        if member[1] == "moc":
            if _contains_model_moc(env, model):
                target_prefix, target_moc = member[0], data
                break
            stub_names = (
                f"assets/art_live2d_{model.casefold()}.bundle",
                f"assets/art_live2d_characters_{model.casefold()}.bundle",
            )
            if allow_stub_moc and _asset_bundle_name(env).casefold() in stub_names:
                target_prefix, target_moc = member[0], data
                break
    if target_prefix is None or target_moc is None:
        raise RuntimeError(f"chunk 中未找到目标模型 MOC3: {model}")

    family: dict[str, bytes] = {"moc": target_moc}
    texture: tuple[int, bytes] | None = None
    for index, (data, _next_off) in enumerate(block_source(), 1):
        _cancel(cancel_event)
        if emit:
            emit("正在收集 Live2D 家族", f"第 {index} 块")
        try:
            env = UnityPy.load(data)
        except Exception:  # noqa: BLE001
            continue
        member = _family_member(_asset_bundle_name(env))
        if not member:
            continue
        prefix, kind = member
        if kind == "tex" and (prefix == target_prefix or prefix.startswith(target_prefix + "_")):
            resolution = 0
            match = re.search(r"\.(512|1024|2048|4096)\.bundle$", _asset_bundle_name(env))
            if match:
                resolution = int(match.group(1))
            if texture is None or resolution >= texture[0]:
                texture = (resolution, data)
        elif prefix == target_prefix and kind != "moc":
            family.setdefault(kind, data)
    if texture:
        family["tex"] = texture[1]
    return target_prefix, family


def assemble_live2d(
    model: str,
    blocks,
    out_dir: str | Path,
    *,
    cancel_event=None,
    emit: Callable[[str, str], None] | None = None,
    moc3_bytes: bytes | None = None,
) -> dict:
    """从已下载 chunk 的块迭代器装配一个 Cubism 模型。

    moc3_bytes：classify 通道的 moc bundle 是 PTN 特殊容器（MOC3 数据在
    UnityPy 可读范围之外），调用方自行提取后传入；传 non-None 时同时允许
    collect_model_family 以 AssetBundle 名认定 moc。
    """
    import UnityPy

    _cancel(cancel_event)
    prefix, family = collect_model_family(
        blocks, model, cancel_event=cancel_event, emit=emit,
        allow_stub_moc=moc3_bytes is not None,
    )
    out = Path(out_dir)
    textures = out / "textures"
    motions = out / "motions"
    textures.mkdir(parents=True, exist_ok=True)
    motions.mkdir(parents=True, exist_ok=True)

    tex_files: list[str] = []
    if family.get("tex"):
        env = UnityPy.load(family["tex"])
        for obj in env.objects:
            _cancel(cancel_event)
            if obj.type.name != "Texture2D":
                continue
            try:
                texture = obj.read()
                if not (texture.m_Name or "").startswith("texture_"):
                    continue
                relative = f"textures/{texture.m_Name}.png"
                texture.image.save(out / relative)
                tex_files.append(relative)
            except Exception:  # noqa: BLE001
                continue
    if not tex_files:
        raise RuntimeError(f"Live2D 模型 {model} 未提取到 texture_* 贴图")

    motion_groups: dict[str, list[dict]] = {}
    # 高保真转换：AnimationClip 携带完整贝塞尔曲线；sssekai 负责 Unity Clip → motion3。
    # 其输出 ID 是路径 CRC 数字，需要用 MOC3 的 Parts/Parameters 表精确还原。
    if family.get("animation"):
        try:
            from sssekai.fmt.moc3 import read_moc3
            from sssekai.fmt.motion3 import to_motion3
            from sssekai.unity.AnimationClip import AnimationHelper

            moc_bytes = moc3_bytes if moc3_bytes is not None else _extract_moc_bytes(family["moc"], model)
            parts, parameters = read_moc3(io.BytesIO(moc_bytes))
            part_crc = {
                zlib.crc32(("Parts/" + value).encode("utf-8")) & 0xFFFFFFFF: value
                for value in parts
            }
            parameter_crc = {
                zlib.crc32(("Parameters/" + value).encode("utf-8")) & 0xFFFFFFFF: value
                for value in parameters
            }
            crc_cache = {
                **{key: "Parts/" + value for key, value in part_crc.items()},
                **{key: "Parameters/" + value for key, value in parameter_crc.items()},
            }
            env = UnityPy.load(family["animation"])
            converted: list[tuple[str, dict]] = []
            for obj in env.objects:
                _cancel(cancel_event)
                if obj.type.name != "AnimationClip":
                    continue
                try:
                    clip = obj.read()
                    motion = to_motion3(AnimationHelper.from_clip(clip), crc_cache, clip)
                except Exception:  # noqa: BLE001
                    continue
                # 某些 sssekai 版本仍会保留数值 ID / 误标 Target；基于两张 CRC 表修正。
                for curve in motion.get("Curves", []):
                    try:
                        raw_id = int(curve.get("Id"))
                    except (TypeError, ValueError):
                        continue
                    if raw_id in parameter_crc:
                        curve["Id"] = parameter_crc[raw_id]
                        curve["Target"] = "Parameter"
                    elif raw_id in part_crc:
                        curve["Id"] = part_crc[raw_id]
                        curve["Target"] = "PartOpacity"
                converted.append((clip.m_Name, motion))

            if converted:
                # 游戏内背景层与人物层动作同时播放。把背景曲线加法合并进所有
                # 人物动作（只补缺不覆盖），使互动/表情动作播放时背景同步动效，
                # 与游戏原生表现一致。命名兼容两种：xxx_background / Background。
                background_motions = [
                    m for n, m in converted
                    if re.search(r"(?:^|_)background$", n.casefold())
                ]
                if background_motions:
                    for i, (name, motion) in enumerate(converted):
                        if re.search(r"(?:^|_)background$", name.casefold()):
                            continue
                        for bg in background_motions:
                            motion = _add_motion_curves(motion, bg)
                        converted[i] = (name, motion)
                for name, motion in converted:
                    filename = name + ".motion3.json"
                    (motions / filename).write_text(
                        json.dumps(motion, ensure_ascii=False, indent=1), encoding="utf-8"
                    )
                    group = "Idle" if name.casefold().endswith("_idle") else "Tap"
                    motion_groups.setdefault(group, []).append(
                        {"Name": name, "File": f"motions/{filename}"}
                    )
                # 游戏时间轴默认同时循环 Idle(layer1) + Background(layer0)。合并为单条动作。
                idle = next(((n, m) for n, m in converted if n.casefold().endswith("_idle")), None)
                background = next(((n, m) for n, m in converted if n.casefold().endswith("_background")), None)
                if idle and background:
                    merged = _merge_motion3(idle[1], background[1], "IdleCombined")
                    filename = "IdleCombined.motion3.json"
                    (motions / filename).write_text(
                        json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8"
                    )
                    motion_groups["Idle"] = [{"Name": "IdleCombined", "File": f"motions/{filename}"}]
        except Exception as exc:  # noqa: BLE001
            if emit:
                emit("高保真动作转换失败", str(exc)[:160])

    # 兼容回退：没有 AnimationClip 转换结果时，才使用 .fade 的简化曲线。
    if not motion_groups and family.get("motion"):
        param_table = _param_table(family["moc"])
        env = UnityPy.load(family["motion"])
        for obj in env.objects:
            _cancel(cancel_event)
            if obj.type.name != "MonoBehaviour":
                continue
            try:
                tree = obj.read_typetree()
            except Exception:  # noqa: BLE001
                continue
            name = tree.get("m_Name", "")
            if name.endswith("fadeMotionList"):
                (motions / "_fadeMotionList.json").write_text(
                    json.dumps(tree, ensure_ascii=False), encoding="utf-8"
                )
                continue
            if not name.endswith(".fade"):
                continue
            motion = _fade_to_motion3(tree, param_table)
            if not motion:
                continue
            filename = name[:-len(".fade")] + ".motion3.json"
            (motions / filename).write_text(
                json.dumps(motion, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            group = "Idle" if name.casefold() == "idle.fade" else "Tap"
            motion_groups.setdefault(group, []).append(
                {"Name": filename[:-len(".motion3.json")], "File": f"motions/{filename}"}
            )

    moc3 = moc3_bytes if moc3_bytes is not None else _extract_moc_bytes(family["moc"], model)
    (out / "model.moc3").write_bytes(moc3)

    file_references = {"Moc": "model.moc3", "Textures": tex_files}
    if motion_groups:
        file_references["Motions"] = motion_groups
    model3 = {
        "Version": 3,
        "FileReferences": file_references,
        "Groups": [{"Target": "Parameter", "Name": "EyeBlink", "Ids": []}],
        "HitAreas": [],
    }
    profile_path = MD / "ptn_render_profiles.json"
    if profile_path.exists():
        try:
            profiles = json.loads(profile_path.read_text(encoding="utf-8"))
            profile = profiles.get(model.casefold()) or profiles.get(model)
            if isinstance(profile, dict) and len(profile.get("materialIds", [])) > 0:
                model3["PTNRender"] = {
                    "MaterialIds": profile["materialIds"],
                    "MaterialNames": profile.get("materialNames", []),
                }
        except Exception:  # noqa: BLE001
            pass
    (out / "model3.json").write_text(
        json.dumps(model3, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return {
        "model": model,
        "family": prefix,
        "output": str(out),
        "textures": len(tex_files),
        "motions": sum(len(items) for items in motion_groups.values()),
    }


def assemble_from_chunk(
    model: str,
    chunk_path: str | Path,
    out_dir: str | Path,
    *,
    cancel_event=None,
    emit: Callable[[str, str], None] | None = None,
    extra_blocks: Iterable[tuple[bytes, int]] | None = None,
) -> dict:
    """chunk 装配入口。

    extra_blocks：落在其他 chunk 的家族块（贴图/材质/特效…），由调用方按
    HTTP Range 取回并用 split_ptn_chunk.read_block 解密后传入。PTN 的 Live2D
    家族可能跨 chunk（如 Hameier_5 的 moc 与贴图不在同一 chunk）。
    """
    from tools.split_ptn_chunk import iter_blocks

    extras = list(extra_blocks or ())

    def blocks():
        yield from iter_blocks(Path(chunk_path), cancel_event)
        for item in extras:
            _cancel(cancel_event)
            yield item

    return assemble_live2d(
        model,
        blocks,
        out_dir,
        cancel_event=cancel_event,
        emit=emit,
    )


def _extract_moc_bytes(bundle: bytes, model: str) -> bytes:
    import UnityPy

    env = UnityPy.load(bundle)
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            data = obj.read()
            if (data.m_Name or "").casefold() != model.casefold():
                continue
            payload = getattr(data, "_bytes", None)
            if payload is not None:
                moc = bytes(payload)
                if moc.startswith(b"MOC3"):
                    return moc
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError(f"MOC3 bytes not found: {model}")


def _merge_motion3(first: dict, second: dict, name: str) -> dict:
    from motion3_utils import add_tiled_background_curves, recompute_motion_meta, tile_layer_curves

    merged = json.loads(json.dumps(first))
    by_key = {(curve.get("Target"), curve.get("Id")): curve for curve in merged.get("Curves", [])}
    for curve in second.get("Curves", []):
        by_key[(curve.get("Target"), curve.get("Id"))] = json.loads(json.dumps(curve))
    merged["Curves"] = list(by_key.values())
    meta = merged.setdefault("Meta", {})
    meta["Name"] = name
    meta["Duration"] = max(
        float(first.get("Meta", {}).get("Duration", 0) or 0),
        float(second.get("Meta", {}).get("Duration", 0) or 0),
    )
    meta["Loop"] = True
    # 两层在游戏里独立循环：合并后把短于总时长的一层平铺到总时长，
    # 否则短层会在尾部保持末值（待机人物定住 / 一次性动作结尾静止）。
    second_keys = {(curve.get("Target"), curve.get("Id")) for curve in second.get("Curves", [])}
    add_tiled_background_curves(merged, second, None)
    tile_layer_curves(merged, second_keys, skipped=None)
    recompute_motion_meta(merged)
    return merged


def _curve_counts(curves: list[dict]) -> tuple[int, int]:
    from motion3_utils import motion_curve_counts

    return motion_curve_counts(curves)


def _add_motion_curves(base: dict, extra: dict) -> dict:
    """把 Background 曲线按周期铺满人物动作 Duration，冲突时保留人物曲线。"""
    from motion3_utils import add_tiled_background_curves

    return add_tiled_background_curves(base, extra)


def _param_table(moc3_probe: bytes) -> list[str]:
    import UnityPy

    moc3 = b""
    try:
        env = UnityPy.load(moc3_probe)
        for obj in env.objects:
            if obj.type.name == "MonoBehaviour":
                raw = obj.get_raw_data()
                pos = raw.find(b"MOC3")
                if pos >= 0:
                    moc3 = raw[pos:]
                    break
    except Exception:  # noqa: BLE001
        return []
    seen, output = set(), []
    for match in re.finditer(rb"Param[A-Za-z0-9_]{1,30}", moc3):
        value = match.group().decode()
        if value not in seen:
            seen.add(value)
            output.append(value)
    return output


def _fade_to_motion3(tree: dict, param_table: list[str]) -> dict | None:
    curves, segment_total, point_total = [], 0, 0
    ids = tree.get("ControlIds", [])
    tracks = tree.get("ControlTracks", [])
    duration = float(tree.get("MotionLength", 0) or 0)
    for control_id, track in zip(ids, tracks):
        if not isinstance(track, dict):
            continue
        curve = track.get("m_Curve") or []
        if not curve:
            continue
        parameter = None
        if isinstance(control_id, int) and 1 <= control_id <= len(param_table):
            parameter = param_table[control_id - 1]
        parameter = parameter or f"Param{control_id}"
        points = [(float(key["time"]), float(key["value"])) for key in curve]
        segments = [points[0][0], points[0][1]]
        for _previous, current in zip(points, points[1:]):
            segments.extend([1, current[0], current[1]])
        curves.append({"Target": "Parameter", "Id": parameter, "Segments": segments})
        segment_total += max(0, len(points) - 1)
        point_total += len(points)
    if not curves:
        return None
    return {
        "Version": 3,
        "Meta": {
            "Duration": duration,
            "Fps": 30,
            "Loop": True,
            "CurveCount": len(curves),
            "TotalSegmentCount": segment_total,
            "TotalPointCount": point_total,
        },
        "Curves": curves,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model")
    parser.add_argument("--chunk", default="", help="已有 chunk 路径；为空时按索引下载")
    parser.add_argument("--output", default="", help="输出模型目录")
    args = parser.parse_args()

    chunk_path = Path(args.chunk) if args.chunk else None
    downloaded = False
    if chunk_path is None:
        from plugins.sources.ptn_cdn import PtnCdnSource

        entry = find_model_entry(args.model)
        source = PtnCdnSource()
        WORK.mkdir(parents=True, exist_ok=True)
        chunk_path = source.download_chunk(entry["bundle"], WORK)
        downloaded = True
    output = Path(args.output) if args.output else OUT_ROOT / args.model
    try:
        result = assemble_from_chunk(args.model, chunk_path, output)
        print(f"已装配: {result['output']} (贴图 {result['textures']}, 动作 {result['motions']})")
        return 0
    finally:
        if downloaded and chunk_path:
            chunk_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
