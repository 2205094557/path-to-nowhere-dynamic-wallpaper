"""静态立绘提取插件：纯 UnityPy 兜底实现（Texture2D / Sprite / 网格缝合）。

优先级：
  1. prefab/tex 包内 Mesh + Texture2D 三角形缝合（还原多层立绘的错位拼接）；
  2. 兜底：直接导出包内最大的 Texture2D / Sprite 图像。
多层合成引擎（按贴图层叠还原完整立绘）由后续里程碑按具体资源格式补充。
"""

from __future__ import annotations

from pathlib import Path

import UnityPy
from PIL import Image

from core.applog import setup_logging
from core.registry import ExtractorPlugin

UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"
logger = setup_logging()


def _stitch_simple(mesh, texture):
    """兜底：单网格单纹理的三角形缝合。"""
    lines = mesh.export().splitlines()
    draw_pic, tex_pos, faces = [], [], []
    for line in lines:
        if line.startswith("v "):
            p = line.split()
            draw_pic.append((int(float(p[1])), int(float(p[2]))))
        elif line.startswith("vt "):
            p = line.split()
            tex_pos.append((float(p[1]), float(p[2])))
        elif line.startswith("f "):
            p = line.split()
            if len(p) >= 4:
                faces.append([int(p[i].split("/")[0]) for i in range(1, 4)])
    if not faces or not draw_pic:
        return None
    w, h = texture.m_Width, texture.m_Height
    tex_px = [(round(u * w), round((1 - v) * h)) for u, v in tex_pos]
    img = texture.image
    xmin = min(x for x, y in draw_pic)
    xmax = max(x for x, y in draw_pic)
    ymin = min(y for x, y in draw_pic)
    ymax = max(y for x, y in draw_pic)
    flipped = [(x - xmin, ymax - y) for x, y in draw_pic]
    canvas = Image.new("RGBA", (xmax - xmin, ymax - ymin), (255, 255, 255, 0))
    for face in faces:
        try:
            i0, i1, i2 = face
            pts = [flipped[i0 - 1], flipped[i1 - 1], flipped[i2 - 1]]
            cuts = [tex_px[i0 - 1], tex_px[i1 - 1], tex_px[i2 - 1]]
            px = min(p[0] for p in pts)
            py = min(p[1] for p in pts)
            cut = img.crop(
                (
                    min(c[0] for c in cuts),
                    min(c[1] for c in cuts),
                    max(c[0] for c in cuts),
                    max(c[1] for c in cuts),
                )
            )
            canvas.paste(cut, (px, py))
        except Exception:  # noqa: BLE001
            continue
    return canvas


def _get_mesh_texture(tex_path):
    mesh = texture = None
    env = UnityPy.load(str(tex_path))
    for asset in env.assets:
        for value in asset.values():
            if value.type.name == "Mesh":
                mesh = value.read()
            elif value.type.name == "Texture2D":
                texture = value.read()
    return mesh, texture


def _largest_image(path: str):
    """取包内最大的 Texture2D / Sprite 图像（纯贴图导出兜底）。"""
    try:
        env = UnityPy.load(str(path))
    except Exception:  # noqa: BLE001
        return None
    best = None
    best_area = 0
    for obj in env.objects:
        if obj.type.name not in ("Texture2D", "Sprite"):
            continue
        try:
            img = obj.read().image
        except Exception:  # noqa: BLE001
            continue
        if img is None:
            continue
        area = img.width * img.height
        if area > best_area:
            best_area = area
            best = img
    return best


class StaticExtractor(ExtractorPlugin):
    id = "static"
    name = "静态立绘提取"

    def extract(self, prefab_path: str, tex_path: str, out_dir: str) -> dict:
        painting = Path(prefab_path).name
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)

        # 主路径：Mesh + Texture2D 三角形缝合
        mesh, texture = _get_mesh_texture(tex_path)
        if mesh and texture:
            canvas = _stitch_simple(mesh, texture)
            if canvas is not None:
                canvas.save(out / "painting.png")
                return {"image": "painting.png", "size": list(canvas.size), "mode": "simple"}
        # 兜底：直接导出包内最大的 Texture2D / Sprite 贴图
        for cand in (tex_path, prefab_path):
            img = _largest_image(cand)
            if img is not None:
                logger.warning("static extract %s 降级为 direct 贴图（无网格）", painting)
                img.save(out / "painting.png")
                return {"image": "painting.png", "size": list(img.size), "mode": "direct"}
        raise RuntimeError("未找到可用的网格或贴图")
