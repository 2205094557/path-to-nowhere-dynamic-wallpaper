"""静态立绘壁纸导出插件：把静态立绘 PNG 生成为 WE web 壁纸项目。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from core.registry import ExporterPlugin
from core.wallpaper import (
    WALLPAPER_LAYOUT,
    bg_css_for_skin,
    export_defaults,
    generate_preview_image,
    layout_storage_key,
    project_dir_name,
    project_json,
    render_template,
    wallpaper_title,
)


class WallpaperStaticExporter(ExporterPlugin):
    id = "wallpaper_static"
    name = "静态立绘壁纸导出"

    def export(self, skin: dict, options: dict, out_dir: str) -> str:
        out = Path(out_dir)
        proj = out / project_dir_name(skin["character"], skin["name"])
        assets = proj / "assets"
        assets.mkdir(parents=True, exist_ok=True)

        src_dir = Path(options["root"]) / "resources" / skin["asset"]["dir"]
        image = skin["asset"].get("image", "painting.png")
        upscale_version = options.get("upscaleVersion") or "none"
        if upscale_version != "none":
            up_img = f"painting_{upscale_version}.png"
            if (src_dir / up_img).is_file():
                image = up_img
        shutil.copy2(src_dir / image, assets / image)
        shutil.copy2(WALLPAPER_LAYOUT, proj / "wallpaper-layout.js")

        html = render_template(
            "wallpaper_static.html",
            IMAGE="assets/" + image,
            LAYOUT_STORAGE_KEY=json.dumps(layout_storage_key(skin, "static"), ensure_ascii=False),
            BG_CSS=bg_css_for_skin(
                skin, options.get("root"), options.get("bg", "monet"), options.get("bgColor")
            ),
            **export_defaults(options),
        )
        (proj / "index.html").write_text(html, encoding="utf-8")
        (proj / "project.json").write_text(
            json.dumps(
                project_json(
                    wallpaper_title(skin["character"], skin["name"]),
                    scale=options.get("scale", 100),
                    offset_x=options.get("offsetX", 0),
                    offset_y=options.get("offsetY", 0),
                    alignment=options.get("alignment", 0),
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        # 静态立绘原图就是完整立绘，直接合成封面；取色失败时用固定渐变。
        generate_preview_image(
            proj,
            image_file=src_dir / image,
            bg=options.get("bg", "monet"),
            bg_color=options.get("bgColor"),
        )
        return str(proj)
