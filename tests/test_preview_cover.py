from __future__ import annotations

import base64
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw

from core.wallpaper import (
    PREVIEW_NAME,
    generate_preview_image,
    project_json,
    render_cover_background,
    write_preview_from_data_url,
)


def png_data_url(width: int = 320, height: int = 180) -> str:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (width // 3, height // 4, width * 2 // 3, height * 3 // 4),
        fill=(220, 30, 30, 255),
    )
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


class PreviewCoverTests(unittest.TestCase):
    def test_project_json_points_to_preview_jpg(self):
        self.assertEqual(project_json("title")["preview"], PREVIEW_NAME)

    def test_background_modes_render_rgb(self):
        for mode in ("auto", "monet", "frost", "star", "solid"):
            image = render_cover_background(
                320, 180, ["#705090", "#223355"], mode, "#123456"
            )
            self.assertEqual(image.size, (320, 180))
            self.assertEqual(image.mode, "RGB")

    def test_data_url_composites_character_over_background(self):
        with tempfile.TemporaryDirectory() as temp:
            proj = Path(temp)
            out = write_preview_from_data_url(
                proj,
                png_data_url(),
                palette=["#102030"],
                bg="solid",
                bg_color="#102030",
            )
            self.assertIsNotNone(out)
            self.assertEqual(out.name, PREVIEW_NAME)
            self.assertTrue(out.is_file())
            with Image.open(out) as image:
                rgb = image.convert("RGB")
                center = rgb.getpixel((rgb.width // 2, rgb.height // 2))
                corner = rgb.getpixel((2, 2))
            # 中心是截图里的红色角色，角落是后端重绘的深色背景
            self.assertGreater(center[0], 120)
            self.assertLess(corner[0], 80)

    def test_invalid_data_url_returns_none(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(write_preview_from_data_url(Path(temp), "not-a-png"))
            self.assertIsNone(write_preview_from_data_url(Path(temp), ""))

    def test_static_image_cover_and_placeholder(self):
        with tempfile.TemporaryDirectory() as temp:
            proj = Path(temp) / "static"
            proj.mkdir()
            painting = Path(temp) / "painting.png"
            Image.new("RGBA", (400, 600), (10, 200, 10, 255)).save(painting)
            out = generate_preview_image(
                proj, image_file=painting, bg="solid", bg_color="#000000"
            )
            self.assertTrue(out.is_file())
            with Image.open(out) as image:
                self.assertLessEqual(max(image.size), 1280)

            placeholder_dir = Path(temp) / "live2d"
            placeholder_dir.mkdir()
            placeholder = generate_preview_image(placeholder_dir, title="角色 · 装束")
            self.assertTrue(placeholder.is_file())
            with Image.open(placeholder) as image:
                self.assertEqual(image.size, (1280, 720))

            # 已有封面不被兜底覆盖（前端截图写入后不应被二次生成冲掉）
            before = out.read_bytes()
            generate_preview_image(proj, image_file=painting)
            self.assertEqual(out.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
