"""core/palette 回归：纯色背景取色 + 超分贴图选源 + 采样密度。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.palette import extract_palette, find_palette_source, mode_css  # noqa: E402


class ModeCssTests(unittest.TestCase):
    def test_solid_with_palette(self):
        self.assertEqual(mode_css("solid", ["#123456"]), "background: #123456;")

    def test_solid_explicit_color_without_palette(self):
        # 取色失败（palette=None）时，用户显式选的颜色仍必须生效
        self.assertEqual(mode_css("solid", None, "#ff8800"), "background: #ff8800;")

    def test_solid_no_color_no_palette(self):
        self.assertEqual(mode_css("solid", None), "")

    def test_solid_invalid_color_falls_back_to_palette(self):
        # 恶意/非法颜色不拼进 CSS，回退到调色板首色
        self.assertEqual(mode_css("solid", ["#123456"], "red"), "background: #123456;")
        self.assertEqual(mode_css("solid", ["#123456"], ""), "background: #123456;")

    def test_non_solid_empty_palette(self):
        self.assertEqual(mode_css("auto", None), "")
        self.assertEqual(mode_css("monet", None), "")


def _solid_png(path: Path, size: int = 512) -> None:
    Image.new("RGBA", (size, size), (30, 60, 120, 255)).save(path)


def _quad_png(path: Path, size: int) -> None:
    """四象限四色图：用于验证采样确实覆盖整张图。"""
    im = Image.new("RGBA", (size, size), (30, 60, 120, 255))
    half = size // 2
    colors = [(200, 40, 40, 255), (40, 200, 60, 255), (220, 180, 40, 255), (90, 60, 210, 255)]
    for i, color in enumerate(colors):
        x, y = (0 if i % 2 == 0 else half), (0 if i < 2 else half)
        im.paste(color, (x, y, x + half, y + half))
    im.save(path)


class SourceAndExtractTests(unittest.TestCase):
    def test_live2d_picks_texture00_over_upscaled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "resources" / "extracted" / "live2d" / "X_1" / "textures"
            d.mkdir(parents=True)
            _solid_png(d / "texture_00.png", 64)
            _solid_png(d / "texture_00_realesr_4x.png", 512)
            _solid_png(d / "_tmp_rgb_texture_00_realesr_4x.png", 512)
            skin = {"type": "live2d", "asset": {"dir": "extracted/live2d/X_1"}}
            got = find_palette_source(skin, root)
            self.assertEqual(got.name, "texture_00.png")

    def test_extract_palette_on_upscaled_texture(self):
        # 超分贴图常超过 PIL 解压炸弹阈值，本地受信文件必须能正常解码
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "huge.png"
            _quad_png(p, 8192)
            pal = extract_palette(p)
            self.assertTrue(pal)
            # 采样步长修正后应扫到各色块，而不是只命中少数像素
            self.assertEqual(len(pal), 4)

    def test_sampling_density_on_large_texture(self):
        # 回归：旧 step=(w*h)//80000 会让 8192² 只采样约 100 个像素
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "quad.png"
            _quad_png(p, 4096)
            pal = extract_palette(p)
            self.assertEqual(len(pal), 4)

    def test_extract_palette_invalid_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.png"
            p.write_bytes(b"not a png")
            self.assertIsNone(extract_palette(p))


if __name__ == "__main__":
    unittest.main()
