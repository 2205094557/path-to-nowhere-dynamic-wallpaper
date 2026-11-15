# -*- coding: utf-8 -*-
"""Cubism motion3 曲线解析、计数与背景周期平铺工具。"""

from __future__ import annotations

import copy
import math
from typing import Iterable

SEGMENT_SIZES = {0: 3, 1: 7, 2: 3, 3: 3}
TARGET_ORDER = {"Model": 0, "Parameter": 1, "PartOpacity": 2}
EPSILON = 1e-7


def parse_curve_segments(raw: list) -> tuple[tuple[float, float], list[dict]]:
    """解析 [initial point, typed segments...]，拒绝损坏或时间倒退的数据。"""
    if not isinstance(raw, list) or len(raw) < 2:
        raise ValueError("motion curve Segments 缺少初始点")
    values = [float(value) for value in raw]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("motion curve Segments 含非有限数值")
    initial = (values[0], values[1])
    segments: list[dict] = []
    cursor = 2
    previous_time = initial[0]
    while cursor < len(values):
        tag_value = values[cursor]
        tag = int(tag_value)
        if float(tag) != tag_value or tag not in SEGMENT_SIZES:
            raise ValueError(f"未知 motion segment 类型: {tag_value}")
        size = SEGMENT_SIZES[tag]
        if cursor + size > len(values):
            raise ValueError("motion segment 数据截断")
        payload = values[cursor + 1: cursor + size]
        if tag == 1:
            controls = [(payload[0], payload[1]), (payload[2], payload[3])]
            end = (payload[4], payload[5])
            if controls[0][0] < previous_time or controls[1][0] < controls[0][0] or end[0] < controls[1][0]:
                raise ValueError("Bezier 控制点时间倒退")
        else:
            controls = []
            end = (payload[0], payload[1])
            if end[0] < previous_time:
                raise ValueError("motion segment 端点时间倒退")
        segments.append({"type": tag, "controls": controls, "end": end})
        previous_time = end[0]
        cursor += size
    return initial, segments


def serialize_curve_segments(initial: tuple[float, float], segments: Iterable[dict]) -> list:
    output: list[float | int] = [initial[0], initial[1]]
    for segment in segments:
        tag = int(segment["type"])
        output.append(tag)
        if tag == 1:
            for point in segment["controls"]:
                output.extend((point[0], point[1]))
        output.extend((segment["end"][0], segment["end"][1]))
    return output


def curve_counts(curve: dict) -> tuple[int, int]:
    _, segments = parse_curve_segments(curve.get("Segments", []))
    return len(segments), 1 + sum(3 if segment["type"] == 1 else 1 for segment in segments)


def curve_end_time(curve: dict) -> float:
    """曲线的最后一个关键帧时间。"""
    initial, segments = parse_curve_segments(curve.get("Segments", []))
    return segments[-1]["end"][0] if segments else initial[0]


def motion_character_end(motion: dict, background_keys: set) -> float:
    """人物自有曲线（排除 Background-owned 键）的最晚关键帧时间。

    合并后的动作里背景层常比人物层长（人物循环 ~12.7s、背景循环 21.6s）。
    若直接按 Meta.Duration（背景长度）循环，人物会先停住再等背景播完，
    表现为「待机动画播着播着人物定住，过一会才继续」。
    """
    end = 0.0
    for curve in motion.get("Curves", []):
        if (curve.get("Target"), curve.get("Id")) in background_keys:
            continue
        end = max(end, curve_end_time(curve))
    return end


def scale_curve_time(curve: dict, factor: float) -> dict:
    """整体缩放曲线时间轴（保留数值），用于把背景周期压缩/拉伸到人物时长。"""
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("时间缩放系数必须为正数")
    initial, segments = parse_curve_segments(curve.get("Segments", []))
    scaled = copy.deepcopy(curve)
    output: list[dict] = []
    for segment in segments:
        output.append({
            "type": int(segment["type"]),
            "controls": [(time * factor, value) for time, value in segment["controls"]],
            "end": (segment["end"][0] * factor, segment["end"][1]),
        })
    scaled["Segments"] = serialize_curve_segments((initial[0] * factor, initial[1]), output)
    return scaled


def tile_layer_curves(motion: dict, background_keys: set, tolerance: float = 0.02,
                      skipped: list | None = None) -> bool:
    """把短于 Meta.Duration 的人物层曲线按层周期平铺，消除待机尾部的整体静止。

    游戏内 Idle 层与 Background 层是两条独立循环；合并成单条动作后取两层时长的
    max 作为循环长度，较短的人物层会在尾部保持末值——表现就是「待机一会儿后
    人物定住，只剩呼吸/物理/视线在动」。这里按游戏语义把人物层以层周期
    （人物层最晚关键帧）平铺到总时长。

    只平铺首末闭环（|v(0)-v(末关键帧)| <= tolerance）的曲线；不闭环的曲线
    （例如一次性开关参数）保持末值，平铺它们会在每个周期边界引入跳变。
    返回是否修改了 motion。
    """
    duration = float(motion.get("Meta", {}).get("Duration", 0) or 0)
    restricted = bool(motion.get("Meta", {}).get("AreBeziersRestricted", True))
    layer_end = motion_character_end(motion, background_keys)
    if layer_end <= EPSILON or duration - layer_end <= EPSILON:
        return False
    curves = motion.get("Curves", [])
    changed = False
    for index, curve in enumerate(curves):
        if (curve.get("Target"), curve.get("Id")) in background_keys:
            continue
        try:
            initial, segments = parse_curve_segments(curve.get("Segments", []))
        except ValueError:
            continue
        end_time = segments[-1]["end"][0] if segments else initial[0]
        if end_time >= duration - EPSILON or abs(initial[0]) > EPSILON:
            continue
        if abs(initial[1] - segments[-1]["end"][1]) > tolerance:
            if skipped is not None:
                skipped.append((curve.get("Target"), curve.get("Id")))
            continue
        # 平铺单元必须覆盖整个层周期：曲线自身提前结束时补保持段，
        # 否则周期边界会提前重放该曲线（例如每半周期闪一次的显隐）。
        unit = copy.deepcopy(curve)
        if end_time < layer_end - EPSILON:
            hold = {"type": 0, "controls": [], "end": (layer_end, segments[-1]["end"][1])}
            unit["Segments"] = serialize_curve_segments(initial, segments + [hold])
        curves[index] = tile_background_curve(unit, layer_end, duration, restricted)
        changed = True
    if changed:
        recompute_motion_meta(motion)
    return changed


def trim_motion_tail_to_layer(motion: dict, background_keys: set, min_gap: float = 0.15) -> bool:
    """一次性动作：把 Meta.Duration 裁到人物层末尾，消除互动结尾的长静止。

    合并管线曾把一次性动作的 Duration 撑到背景层时长，人物层播完后会静止
    等待动作结束。运行时按 Duration 判定动作完成，裁短后互动会在人物停止
    的瞬间切回待机。背景层超出部分的关键帧保留在文件里（不可达，无害）。
    返回是否修改了 motion。
    """
    duration = float(motion.get("Meta", {}).get("Duration", 0) or 0)
    layer_end = motion_character_end(motion, background_keys)
    if layer_end <= EPSILON or duration - layer_end < min_gap:
        return False
    motion["Meta"]["Duration"] = layer_end
    recompute_motion_meta(motion)
    return True


def normalize_motion_tail(motion: dict, background_keys: set, min_gap: float = 0.2) -> bool:
    """把比人物动作更长的背景曲线压缩到人物时长，消除循环空档。

    合并后的动作里人物层与背景层是两条独立循环（人物 ~12.7s、背景 21.6s），
    合并成单条动作后按 Meta.Duration 循环，人物会先停住、等背景播完才重来。
    这里以人物自有曲线的最晚关键帧为动作时长，把更长的背景曲线等比压缩；
    首末值保持相等，因此循环点没有接缝。返回是否修改了 motion。
    """
    duration = float(motion.get("Meta", {}).get("Duration", 0) or 0)
    character_end = motion_character_end(motion, background_keys)
    if character_end <= EPSILON or duration - character_end < min_gap:
        return False
    curves = motion.get("Curves", [])
    changed = False
    for index, curve in enumerate(curves):
        if (curve.get("Target"), curve.get("Id")) not in background_keys:
            continue
        end = curve_end_time(curve)
        if end > character_end + EPSILON:
            curves[index] = scale_curve_time(curve, character_end / end)
            changed = True
    if not changed:
        return False
    motion["Meta"]["Duration"] = character_end
    recompute_motion_meta(motion)
    return True


def motion_curve_counts(curves: Iterable[dict]) -> tuple[int, int]:
    segment_count = point_count = 0
    for curve in curves:
        segments, points = curve_counts(curve)
        segment_count += segments
        point_count += points
    return segment_count, point_count


def recompute_motion_meta(motion: dict) -> None:
    curves = motion.get("Curves", [])
    indexed = list(enumerate(curves))
    indexed.sort(key=lambda pair: (TARGET_ORDER.get(str(pair[1].get("Target", "")), 3), pair[0]))
    motion["Curves"] = [curve for _, curve in indexed]
    segments, points = motion_curve_counts(motion["Curves"])
    meta = motion.setdefault("Meta", {})
    meta["CurveCount"] = len(motion["Curves"])
    meta["TotalSegmentCount"] = segments
    meta["TotalPointCount"] = points


def _lerp_point(first: tuple[float, float], second: tuple[float, float], amount: float) -> tuple[float, float]:
    return (
        first[0] + (second[0] - first[0]) * amount,
        first[1] + (second[1] - first[1]) * amount,
    )


def _bezier_point(points: list[tuple[float, float]], amount: float) -> tuple[float, float]:
    p01 = _lerp_point(points[0], points[1], amount)
    p12 = _lerp_point(points[1], points[2], amount)
    p23 = _lerp_point(points[2], points[3], amount)
    p012 = _lerp_point(p01, p12, amount)
    p123 = _lerp_point(p12, p23, amount)
    return _lerp_point(p012, p123, amount)


def _bezier_parameter_for_time(points: list[tuple[float, float]], target_time: float, restricted: bool) -> float:
    if restricted:
        span = points[3][0] - points[0][0]
        if span <= EPSILON:
            raise ValueError("无法裁剪零时长 Bezier")
        return max(0.0, min(1.0, (target_time - points[0][0]) / span))
    # Cubism 非 restricted Bezier 以控制点时间求 cubic 参数；时间单调时二分稳定求根。
    low, high = 0.0, 1.0
    for _ in range(60):
        middle = (low + high) / 2
        if _bezier_point(points, middle)[0] < target_time:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def _split_bezier_left(points: list[tuple[float, float]], amount: float) -> list[tuple[float, float]]:
    p0, p1, p2, p3 = points
    p01 = _lerp_point(p0, p1, amount)
    p12 = _lerp_point(p1, p2, amount)
    p23 = _lerp_point(p2, p3, amount)
    p012 = _lerp_point(p01, p12, amount)
    p123 = _lerp_point(p12, p23, amount)
    p0123 = _lerp_point(p012, p123, amount)
    return [p0, p01, p012, p0123]


def _shift_segment(segment: dict, delta: float) -> dict:
    shifted = copy.deepcopy(segment)
    shifted["controls"] = [(time + delta, value) for time, value in segment["controls"]]
    shifted["end"] = (segment["end"][0] + delta, segment["end"][1])
    return shifted


def _trim_segment(start: tuple[float, float], segment: dict, target_time: float, restricted: bool) -> dict:
    end = segment["end"]
    if end[0] <= target_time + EPSILON:
        return copy.deepcopy(segment)
    if target_time < start[0] - EPSILON or end[0] <= start[0]:
        raise ValueError("无法裁剪无效 motion segment")
    tag = int(segment["type"])
    if tag == 0:
        amount = (target_time - start[0]) / (end[0] - start[0])
        return {"type": 0, "controls": [], "end": (target_time, _lerp_point(start, end, amount)[1])}
    if tag == 1:
        points = [start, *segment["controls"], end]
        amount = _bezier_parameter_for_time(points, target_time, restricted)
        left = _split_bezier_left(points, amount)
        # 二分/浮点误差下显式钉住裁剪终点时间。
        left[-1] = (target_time, left[-1][1])
        return {"type": 1, "controls": left[1:3], "end": left[3]}
    if tag == 2:
        return {"type": 2, "controls": [], "end": (target_time, start[1])}
    return {"type": 3, "controls": [], "end": (target_time, end[1])}


def tile_background_curve(curve: dict, period: float, target_duration: float,
                          restricted: bool = True) -> dict:
    """将一个 canonical Background curve 周期平铺到 target_duration。"""
    if period <= EPSILON or target_duration <= EPSILON:
        return copy.deepcopy(curve)
    initial, source_segments = parse_curve_segments(curve.get("Segments", []))
    if not source_segments:
        return copy.deepcopy(curve)
    if abs(initial[0]) > EPSILON:
        raise ValueError(f"Background curve 初始时间不是 0: {initial[0]}")
    # 一周期内且无需裁剪时保留原始编码，避免无意义文件改写。
    if target_duration >= source_segments[-1]["end"][0] - EPSILON and target_duration <= period + EPSILON:
        return copy.deepcopy(curve)

    output: list[dict] = []
    cycle = 0
    while cycle * period < target_duration - EPSILON:
        delta = cycle * period
        previous = (initial[0] + delta, initial[1])
        cycle_end = min((cycle + 1) * period, target_duration)
        for source_segment in source_segments:
            shifted = _shift_segment(source_segment, delta)
            if shifted["end"][0] > cycle_end + EPSILON:
                output.append(_trim_segment(previous, shifted, cycle_end, restricted))
                previous = output[-1]["end"]
                break
            output.append(shifted)
            previous = shifted["end"]
            if previous[0] >= cycle_end - EPSILON:
                break
        # 源曲线早于 Background period 结束时，沿用 Cubism 的末值保持语义。
        if previous[0] < cycle_end - EPSILON:
            output.append({"type": 2, "controls": [], "end": (cycle_end, previous[1])})
            previous = output[-1]["end"]
        if cycle_end >= target_duration - EPSILON:
            break
        # 中间周期边界重置为源初值；zero-duration stepped endpoint 在边界后生效。
        if abs(previous[1] - initial[1]) > EPSILON:
            output.append({"type": 2, "controls": [], "end": (cycle_end, initial[1])})
        cycle += 1

    tiled = copy.deepcopy(curve)
    tiled["Segments"] = serialize_curve_segments(initial, output)
    return tiled


def is_background_owned(existing: dict, source: dict) -> bool:
    """旧合并产物以 canonical source 为首周期；人物冲突曲线不得覆盖。"""
    existing_raw = existing.get("Segments", [])
    source_raw = source.get("Segments", [])
    return len(existing_raw) >= len(source_raw) and existing_raw[:len(source_raw)] == source_raw


def add_tiled_background_curves(base: dict, background: dict, conflicts: list | None = None) -> dict:
    """确定性重建 base 的背景-owned 曲线，保留人物自有冲突曲线和原 Duration。"""
    duration = float(base.get("Meta", {}).get("Duration", 0) or 0)
    period = float(background.get("Meta", {}).get("Duration", 0) or 0)
    restricted = bool(background.get("Meta", {}).get("AreBeziersRestricted", True))
    curves = base.get("Curves", [])
    positions = {(curve.get("Target"), curve.get("Id")): index for index, curve in enumerate(curves)}
    for source in background.get("Curves", []):
        key = (source.get("Target"), source.get("Id"))
        tiled = tile_background_curve(source, period, duration, restricted)
        position = positions.get(key)
        if position is None:
            positions[key] = len(curves)
            curves.append(tiled)
        elif curves[position].get("Segments", []) == tiled.get("Segments", []):
            # 已是目标平铺形态：保持对象不变，确保二次运行字节级幂等。
            continue
        elif is_background_owned(curves[position], source):
            curves[position] = tiled
        elif conflicts is not None:
            conflicts.append(key)
    base["Curves"] = curves
    base.setdefault("Meta", {})["Duration"] = duration
    recompute_motion_meta(base)
    return base
