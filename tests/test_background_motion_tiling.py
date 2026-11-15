from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from motion3_utils import (  # noqa: E402
    add_tiled_background_curves,
    curve_counts,
    curve_end_time,
    motion_character_end,
    motion_curve_counts,
    normalize_motion_tail,
    parse_curve_segments,
    recompute_motion_meta,
    scale_curve_time,
    serialize_curve_segments,
    tile_background_curve,
    tile_layer_curves,
    trim_motion_tail_to_layer,
)


class Motion3UtilsTests(unittest.TestCase):
    def test_parse_serialize_all_segment_types(self):
        raw = [
            0.0, 0.0,
            0, 1.0, 1.0,
            1, 1.25, 1.2, 1.75, 0.8, 2.0, 0.0,
            2, 3.0, 4.0,
            3, 4.0, 2.0,
        ]
        initial, segments = parse_curve_segments(raw)
        self.assertEqual([segment["type"] for segment in segments], [0, 1, 2, 3])
        self.assertEqual(serialize_curve_segments(initial, segments), raw)
        self.assertEqual(curve_counts({"Segments": raw}), (4, 7))

    def test_recompute_meta_counts_bezier_points(self):
        motion = {
            "Meta": {},
            "Curves": [
                {"Target": "Parameter", "Id": "P", "Segments": [0, 0, 1, 0.3, 1, 0.7, 2, 1, 3]},
                {"Target": "Model", "Id": "Opacity", "Segments": [0, 1, 0, 1, 0]},
            ],
        }
        recompute_motion_meta(motion)
        self.assertEqual([curve["Target"] for curve in motion["Curves"]], ["Model", "Parameter"])
        self.assertEqual(motion["Meta"]["CurveCount"], 2)
        self.assertEqual(motion["Meta"]["TotalSegmentCount"], 2)
        self.assertEqual(motion["Meta"]["TotalPointCount"], 6)

    def test_tile_linear_curve_across_multiple_periods(self):
        curve = {"Target": "Parameter", "Id": "BG", "Segments": [0, 0, 0, 1, 1, 0, 2, 0]}
        tiled = tile_background_curve(curve, period=2.0, target_duration=5.0)
        initial, segments = parse_curve_segments(tiled["Segments"])
        self.assertEqual(initial, (0.0, 0.0))
        self.assertAlmostEqual(segments[-1]["end"][0], 5.0)
        self.assertGreater(len(segments), 2)
        # 周期边界必须回到初值后继续下一周期。
        boundary_values = [segment["end"][1] for segment in segments if abs(segment["end"][0] - 2.0) < 1e-7]
        self.assertIn(0.0, boundary_values)

    def test_tile_restricted_bezier_trims_at_target(self):
        curve = {"Target": "Parameter", "Id": "BG", "Segments": [0, 0, 1, 0.25, 1, 0.75, 1, 1, 0]}
        tiled = tile_background_curve(curve, period=1.0, target_duration=1.5, restricted=True)
        _, segments = parse_curve_segments(tiled["Segments"])
        self.assertAlmostEqual(segments[-1]["end"][0], 1.5)
        self.assertTrue(all(point[0] <= 1.5 + 1e-7 for segment in segments for point in [*segment["controls"], segment["end"]]))

    def test_add_background_preserves_character_curve_and_is_idempotent(self):
        character_curve = {"Target": "Parameter", "Id": "CHAR", "Segments": [0, 0, 0, 3, 1]}
        background_curve = {"Target": "Parameter", "Id": "BG", "Segments": [0, 0, 0, 1, 1, 0, 2, 0]}
        base = {
            "Meta": {"Duration": 5.0, "Loop": True},
            "Curves": [copy.deepcopy(character_curve)],
        }
        background = {
            "Meta": {"Duration": 2.0, "AreBeziersRestricted": True},
            "Curves": [copy.deepcopy(background_curve)],
        }
        result = add_tiled_background_curves(base, background)
        self.assertEqual(result["Curves"][0], character_curve)
        first = json.dumps(result, ensure_ascii=False, sort_keys=True)
        add_tiled_background_curves(result, background)
        second = json.dumps(result, ensure_ascii=False, sort_keys=True)
        self.assertEqual(first, second)
        self.assertEqual(result["Meta"]["Duration"], 5.0)
        self.assertEqual(motion_curve_counts(result["Curves"]),
                         (result["Meta"]["TotalSegmentCount"], result["Meta"]["TotalPointCount"]))

    def test_character_key_conflict_is_preserved(self):
        source = {"Target": "Parameter", "Id": "X", "Segments": [0, 0, 0, 1, 1]}
        custom = {"Target": "Parameter", "Id": "X", "Segments": [0, 2, 0, 1, 3]}
        base = {"Meta": {"Duration": 2.0}, "Curves": [copy.deepcopy(custom)]}
        background = {"Meta": {"Duration": 1.0}, "Curves": [source]}
        conflicts = []
        add_tiled_background_curves(base, background, conflicts)
        self.assertEqual(base["Curves"][0], custom)
        self.assertEqual(conflicts, [("Parameter", "X")])

    def test_scale_curve_time_preserves_values_and_scales_times(self):
        curve = {"Target": "Parameter", "Id": "BG", "Segments": [0, 0, 0, 2, 1, 0, 4, 0]}
        scaled = scale_curve_time(curve, 0.5)
        initial, segments = parse_curve_segments(scaled["Segments"])
        self.assertEqual(initial, (0.0, 0.0))
        self.assertAlmostEqual(curve_end_time(scaled), 2.0)
        self.assertEqual([segment["end"][1] for segment in segments], [1.0, 0.0])
        self.assertEqual([segment["end"][0] for segment in segments], [1.0, 2.0])

    def test_motion_character_end_ignores_background_keys(self):
        motion = {
            "Curves": [
                {"Target": "Parameter", "Id": "CHAR", "Segments": [0, 0, 0, 2, 1]},
                {"Target": "Parameter", "Id": "BG", "Segments": [0, 0, 0, 9, 0]},
            ]
        }
        self.assertEqual(motion_character_end(motion, {("Parameter", "BG")}), 2.0)

    def test_normalize_motion_tail_scales_long_curves_and_is_idempotent(self):
        motion = {
            "Meta": {"Duration": 8.0, "Loop": True},
            "Curves": [
                {"Target": "Parameter", "Id": "CHAR", "Segments": [0, 0, 0, 2, 1, 0, 2, 0]},
                {"Target": "Parameter", "Id": "BG", "Segments": [0, 0, 0, 4, 5, 0, 8, 0]},
            ],
        }
        changed = normalize_motion_tail(motion, {("Parameter", "BG")})
        self.assertTrue(changed)
        self.assertAlmostEqual(motion["Meta"]["Duration"], 2.0)
        bg_curve = next(curve for curve in motion["Curves"] if curve["Id"] == "BG")
        self.assertAlmostEqual(curve_end_time(bg_curve), 2.0)
        # 首末值仍相等 → 循环无接缝。
        initial, segments = parse_curve_segments(bg_curve["Segments"])
        self.assertEqual(initial[1], segments[-1]["end"][1])
        self.assertFalse(normalize_motion_tail(motion, {("Parameter", "BG")}))

    def test_normalize_motion_tail_skips_tiny_gap(self):
        motion = {
            "Meta": {"Duration": 8.0},
            "Curves": [
                {"Target": "Parameter", "Id": "CHAR", "Segments": [0, 0, 0, 7.9, 1]},
                {"Target": "Parameter", "Id": "BG", "Segments": [0, 0, 0, 8, 0]},
            ],
        }
        self.assertFalse(normalize_motion_tail(motion, {("Parameter", "BG")}))
        self.assertEqual(motion["Meta"]["Duration"], 8.0)


class TileLayerCurvesTests(unittest.TestCase):
    """合并待机短层平铺与一次性动作尾部裁剪。"""

    def _motion(self, char_segments, duration=4.0):
        return {
            "Meta": {"Duration": duration, "Loop": True, "AreBeziersRestricted": True},
            "Curves": [
                {"Target": "Parameter", "Id": "BG", "Segments": [0, 0, 0, duration, 0]},
                {"Target": "Parameter", "Id": "CHAR", "Segments": char_segments},
            ],
        }

    BG_KEYS = {("Parameter", "BG")}

    def test_closing_curve_is_tiled_to_duration(self):
        motion = self._motion([0, 0, 0, 1, 1, 0, 2, 0])
        self.assertTrue(tile_layer_curves(motion, self.BG_KEYS))
        curve = next(c for c in motion["Curves"] if c["Id"] == "CHAR")
        self.assertAlmostEqual(curve_end_time(curve), 4.0)
        _, segments = parse_curve_segments(curve["Segments"])
        # 周期 2：t=1 与 t=3 值一致，t=2 回到初值
        self.assertAlmostEqual(segments[-1]["end"][0], 4.0)
        motion2 = json.loads(json.dumps(motion))
        self.assertFalse(tile_layer_curves(motion2, self.BG_KEYS))

    def test_non_closing_curve_is_held(self):
        motion = self._motion([0, 0, 0, 1, 10, 0, 2, 10])
        before = json.dumps(motion, sort_keys=True)
        self.assertFalse(tile_layer_curves(motion, self.BG_KEYS))
        self.assertEqual(before, json.dumps(motion, sort_keys=True))

    def test_early_ending_curve_holds_inside_each_cycle(self):
        # 人物层周期 2（另一条曲线到 2s），本曲线 1s 后结束并闭环
        motion = {
            "Meta": {"Duration": 4.0, "Loop": True, "AreBeziersRestricted": True},
            "Curves": [
                {"Target": "Parameter", "Id": "BG", "Segments": [0, 0, 0, 4.0, 0]},
                {"Target": "Parameter", "Id": "OTHER", "Segments": [0, 0, 0, 2.0, 0]},
                {"Target": "Parameter", "Id": "CHAR", "Segments": [0, 0, 0, 0.5, 5, 0, 1.0, 0]},
            ],
        }
        self.assertTrue(tile_layer_curves(motion, self.BG_KEYS))
        curve = next(c for c in motion["Curves"] if c["Id"] == "CHAR")
        _, segments = parse_curve_segments(curve["Segments"])
        self.assertAlmostEqual(segments[-1]["end"][0], 4.0)
        # 每个周期内 1s~2s 保持末值，t=3.5 与 t=1.5 同值（hold）
        values = [seg["end"][1] for seg in segments]
        self.assertEqual(values.count(5.0), 2)

    def test_trim_motion_tail_to_layer(self):
        motion = self._motion([0, 0, 0, 1, 1, 0, 2.5, 0], duration=4.0)
        self.assertTrue(trim_motion_tail_to_layer(motion, self.BG_KEYS))
        self.assertAlmostEqual(motion["Meta"]["Duration"], 2.5)
        self.assertFalse(trim_motion_tail_to_layer(motion, self.BG_KEYS))



if __name__ == "__main__":
    unittest.main()
