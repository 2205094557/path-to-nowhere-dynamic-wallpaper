from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tools"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from smooth_stepped_motions import analyze_motion, smooth_motion


def _stepped_motion(points: list[tuple[float, float]]) -> dict:
    segments: list[float] = [points[0][0], points[0][1]]
    for time, value in points[1:]:
        segments.extend([2, time, value])
    return {
        "Version": 3,
        "Meta": {"Duration": points[-1][0], "Fps": 60.0, "Loop": True},
        "Curves": [{"Target": "Parameter", "Id": "ParamAngleX", "Segments": segments}],
    }


class SmoothSteppedMotionTests(unittest.TestCase):
    def test_analyze_counts_segment_types(self) -> None:
        motion = _stepped_motion([(0.0, 0.0), (0.033, 1.0), (0.067, 2.0)])
        self.assertEqual(analyze_motion(motion), {2: 2})

    def test_stepped_becomes_linear_and_values_are_preserved(self) -> None:
        motion = _stepped_motion([(0.0, 0.0), (0.033, 1.0), (0.067, 2.0)])
        smoothed, changed, stepped = smooth_motion(motion, 0.9)
        self.assertEqual((changed, stepped), (2, 2))
        self.assertEqual(analyze_motion(smoothed), {0: 2})
        self.assertEqual(
            smoothed["Curves"][0]["Segments"],
            [0.0, 0.0, 0, 0.033, 1.0, 0, 0.067, 2.0],
        )
        self.assertEqual(smoothed["Meta"]["TotalSegmentCount"], 2)

    def test_below_ratio_threshold_is_untouched(self) -> None:
        # 2 个 stepped + 1 个 bezier → 占比 0.67，低于 0.9 阈值，原样保留
        motion = _stepped_motion([(0.0, 0.0), (0.033, 1.0), (0.067, 2.0)])
        motion["Curves"][0]["Segments"].extend([1, 0.08, 2.0, 0.09, 3.0, 0.1, 4.0])
        untouched, changed, stepped = smooth_motion(motion, 0.9)
        self.assertEqual((changed, stepped), (0, 2))
        self.assertEqual(analyze_motion(untouched), {2: 2, 1: 1})

    def test_bezier_segments_are_kept(self) -> None:
        # 9 个 stepped + 1 个 bezier → 占比 0.9，达到阈值后只改 stepped
        points = [(round(0.033 * index, 3), float(index)) for index in range(10)]
        motion = _stepped_motion(points)
        motion["Curves"][0]["Segments"].extend([1, 0.35, 2.0, 0.36, 3.0, 0.37, 4.0])
        smoothed, changed, _ = smooth_motion(motion, 0.9)
        self.assertEqual(changed, 9)
        self.assertEqual(analyze_motion(smoothed), {0: 9, 1: 1})


if __name__ == "__main__":
    unittest.main()
