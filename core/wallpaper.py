"""壁纸项目生成公共工具：模板渲染、project.json、背景样式。"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import uuid
from pathlib import Path

from core.palette import extract_palette, find_palette_source, mode_css

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
L2D_APP = TEMPLATES / "live2d-app.js"
WALLPAPER_LAYOUT = TEMPLATES / "wallpaper-layout.js"

# WE 封面文件：project.json 的 preview 字段指向它。必须是真实存在的文件——
# WE 找不到 preview 会持续报 icon create err，托盘图标与渲染进程都会异常。
PREVIEW_NAME = "preview.jpg"
_PREVIEW_MAX = (1280, 1280)
_PREVIEW_FALLBACK_PALETTE = ["#1d3f6e", "#0e1b33", "#13294d", "#0b0f17"]
_HEX6_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
# Pillow 不可用/封面绘制彻底失败时的兜底 JPEG（160×90 纯色 #182438，376B）。
_FALLBACK_PREVIEW_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUf"
    "GhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/2wBDAQcHBwoIChMKChMoGhYaKCgoKCgoKCgoKCgoKCgo"
    "KCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCj/wAARCABaAKADASIAAhEBAxEB/8QAFQAB"
    "AQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAA"
    "AAT/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCBgLkwAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAD//2Q=="
)


def _vendor_file(name: str) -> Path:
    """运行时 JS 文件定位：开发版在 frontend/public/vendor，打包版在 frontend/dist/vendor（vite 把 public 复制到 dist）。"""
    for base in (ROOT / "frontend" / "public" / "vendor", ROOT / "frontend" / "dist" / "vendor"):
        p = base / name
        if p.exists():
            return p
    return ROOT / "frontend" / "public" / "vendor" / name


L2D_CORE = _vendor_file("live2dcubismcore.min.js")

# 与 wallpaper-layout.js 的 ALIGN_ORDER / project.json combo 选项顺序一致
ALIGN_NAMES = ["center", "left-top", "right-top", "left-bottom", "right-bottom"]
ALIGN_LABELS = ["居中", "左上", "右上", "左下", "右下"]

BG_STYLES = {
    "solid": "background: linear-gradient(180deg, #182438, #0b0f17);",
    "gradient": "background: linear-gradient(135deg, #1d3f6e, #0e1b33 55%, #13294d);",
    "monet": (
        "background: radial-gradient(60% 70% at 20% 25%, rgba(122,92,255,.35), transparent 60%),"
        "radial-gradient(55% 65% at 80% 20%, rgba(58,167,255,.3), transparent 60%),"
        "radial-gradient(70% 80% at 50% 95%, rgba(255,122,92,.22), transparent 60%),"
        "linear-gradient(160deg, #141b2e, #0b0f17);"
    ),
    "frost": (
        "background: radial-gradient(45% 55% at 30% 30%, rgba(58,167,255,.25), transparent 60%),"
        "radial-gradient(45% 55% at 75% 70%, rgba(122,92,255,.22), transparent 60%),"
        "linear-gradient(180deg, #101826, #0b0f17);"
    ),
    "star": (
        "background: radial-gradient(1.5px 1.5px at 20% 30%, rgba(255,255,255,.7), transparent 60%),"
        "radial-gradient(1.5px 1.5px at 60% 15%, rgba(255,255,255,.6), transparent 60%),"
        "radial-gradient(1.5px 1.5px at 80% 45%, rgba(255,255,255,.5), transparent 60%),"
        "radial-gradient(1.5px 1.5px at 40% 70%, rgba(255,255,255,.55), transparent 60%),"
        "radial-gradient(1.5px 1.5px at 90% 80%, rgba(255,255,255,.45), transparent 60%),"
        "linear-gradient(180deg, #101a2e, #080c14);"
    ),
}


def bg_css(bg: str) -> str:
    return BG_STYLES.get(bg, BG_STYLES["monet"])


def bg_css_for_skin(skin: dict, root, bg: str, bg_color: str | None = None) -> str:
    """预览与导出共用的背景 CSS：所有模式都优先按皮肤素材取色生成，取色失败才用固定样式。"""
    src = find_palette_source(skin, root)
    palette = extract_palette(src) if src else None
    # 纯色由 mode_css 独立处理（不依赖取色结果）；其余模式取色失败时回退固定样式
    return mode_css(bg, palette, solid_color=bg_color) or bg_css(bg)


def _hex_rgb(value: str) -> tuple[int, int, int]:
    h = value.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _normalize_palette(palette) -> list[str]:
    colors = [str(c) for c in (palette or []) if isinstance(c, str) and _HEX6_RE.match(str(c))]
    if not colors:
        colors = list(_PREVIEW_FALLBACK_PALETTE)
    while len(colors) < 4:
        colors.append(colors[-1])
    return colors


def preview_palette(skin: dict, root) -> list[str]:
    """取色用于封面背景（与壁纸 HTML 的背景取色同源），失败时回退固定调色板。"""
    src = find_palette_source(skin, root)
    return _normalize_palette(extract_palette(src) if src else None)


def _linear_gradient(width: int, height: int, stops: list[str], angle: float):
    from PIL import Image

    if len(stops) == 1:
        return Image.new("RGB", (width, height), _hex_rgb(stops[0]))
    colors = [_hex_rgb(color) for color in stops]
    theta = math.radians(angle)
    # CSS 角度：0deg 朝上、90deg 朝右；屏幕坐标 y 轴向下，所以 dy 取负余弦。
    dx, dy = math.sin(theta), -math.cos(theta)
    length = abs(width * dx) + abs(height * dy) or 1.0
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    last = len(colors) - 1
    for y in range(height):
        oy = y + 0.5 - height / 2
        for x in range(width):
            t = ((x + 0.5 - width / 2) * dx + oy * dy) / length + 0.5
            t = max(0.0, min(1.0, t))
            pos = t * last
            index = min(int(pos), last - 1)
            frac = pos - index
            c0, c1 = colors[index], colors[index + 1]
            pixels[x, y] = (
                int(c0[0] + (c1[0] - c0[0]) * frac + 0.5),
                int(c0[1] + (c1[1] - c0[1]) * frac + 0.5),
                int(c0[2] + (c1[2] - c0[2]) * frac + 0.5),
            )
    return image


def _radial_glow(
    image, cx_pct: float, cy_pct: float, rx_pct: float, ry_pct: float,
    color: str, alpha: float, stop: float = 0.6,
) -> None:
    """在 RGB 图上叠加一层 CSS radial-gradient 语义的椭圆光斑（低分辨率绘制）。"""
    width, height = image.size
    cr, cg, cb = _hex_rgb(color)
    cx, cy = width * cx_pct / 100.0, height * cy_pct / 100.0
    rx = max(1.0, width * rx_pct / 100.0)
    ry = max(1.0, height * ry_pct / 100.0)
    pixels = image.load()
    for y in range(height):
        dy = (y + 0.5 - cy) / ry
        for x in range(width):
            dx = (x + 0.5 - cx) / rx
            dist = math.sqrt(dx * dx + dy * dy)
            if dist >= stop:
                continue
            a = alpha * (1.0 - dist / stop)
            r, g, b = pixels[x, y]
            pixels[x, y] = (
                int(r + (cr - r) * a + 0.5),
                int(g + (cg - g) * a + 0.5),
                int(b + (cb - b) * a + 0.5),
            )


def _draw_cover_stars(image):
    from PIL import Image, ImageDraw

    width, height = image.size
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    radius = max(1, round(min(width, height) * 0.003))
    for xp, yp, alpha in ((20, 30, .7), (60, 15, .6), (80, 45, .5), (40, 70, .55), (90, 80, .45)):
        x, y = width * xp / 100.0, height * yp / 100.0
        draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                     fill=(255, 255, 255, int(255 * alpha)))
    return Image.alpha_composite(image.convert("RGBA"), layer).convert("RGB")


def render_cover_background(
    width: int, height: int, palette=None, bg: str = "monet", bg_color: str | None = None,
):
    """按导出背景样式栅格化封面背景（与 palette.mode_css 的 CSS 渐变语义一致）。

    渐变本身是低频信号，先在 480px 宽的低分辨率画布上逐像素计算再放大，
    避免 1280×720 全量 Python 循环。星点在高分辨率上补画，保持锐利。
    """
    from PIL import Image

    width, height = max(1, int(width)), max(1, int(height))
    colors = _normalize_palette(palette)
    c0, c1, c2 = colors[0], colors[1], colors[2]
    mode = str(bg or "auto").strip().lower()
    if mode == "gradient":
        # 兼容旧配置：与 palette.mode_css 一致，渐变已合并进「莫奈渐变」
        mode = "monet"

    if mode == "solid":
        fill = bg_color if isinstance(bg_color, str) and _HEX6_RE.match(bg_color) else c0
        return Image.new("RGB", (width, height), _hex_rgb(fill))

    low_w = 480
    low_h = max(1, round(low_w * height / width))
    if mode == "monet":
        base = _linear_gradient(low_w, low_h, [c0, "#0b0f17"], 160)
        _radial_glow(base, 20, 25, 60, 70, c0, .35)
        _radial_glow(base, 80, 20, 55, 65, c1, .30)
        _radial_glow(base, 50, 95, 70, 80, c2, .22)
    elif mode == "frost":
        base = _linear_gradient(low_w, low_h, [c0, "#0b0f17"], 180)
        _radial_glow(base, 30, 30, 45, 55, c0, .25)
        _radial_glow(base, 75, 70, 45, 55, c1, .22)
    elif mode == "star":
        base = _linear_gradient(low_w, low_h, [c0, "#080c14"], 180)
    else:  # auto / gradient / 未知模式
        base = _linear_gradient(low_w, low_h, colors, 120)

    image = base if base.size == (width, height) else base.resize((width, height), Image.Resampling.BICUBIC)
    return _draw_cover_stars(image) if mode == "star" else image


def _open_preview_source(path):
    """打开封面素材；超分图可达上亿像素，临时解除 PIL 的解压炸弹上限。"""
    from PIL import Image

    old_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None
    try:
        with Image.open(path) as im:
            return im.convert("RGBA")
    finally:
        Image.MAX_IMAGE_PIXELS = old_limit


def _cover_font(size: int):
    from PIL import ImageFont

    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def _draw_cover_title(image, title: str, subtitle: str = "") -> None:
    """占位封面上的文字（只在没有任何立绘素材时使用）。"""
    from PIL import ImageDraw

    title = str(title or "").strip()
    if not title:
        return
    width, height = image.size
    draw = ImageDraw.Draw(image)
    font = _cover_font(max(20, int(height * 0.055)))
    sub_font = _cover_font(max(14, int(height * 0.032)))
    for text, use_font, offset_y in ((title, font, 0), (subtitle, sub_font, int(height * 0.08))):
        if not text:
            continue
        try:
            box = draw.textbbox((0, 0), text, font=use_font)
            x = (width - (box[2] - box[0])) / 2
            y = height * 0.52 + offset_y
            draw.text((x + 2, y + 2), text, font=use_font, fill=(0, 0, 0))
            draw.text((x, y), text, font=use_font, fill=(235, 240, 250))
        except Exception:  # noqa: BLE001
            continue


def _save_preview(image, proj: Path) -> Path:
    from PIL import Image

    image = image.convert("RGB")
    image.thumbnail(_PREVIEW_MAX, Image.Resampling.LANCZOS)
    preview = Path(proj) / PREVIEW_NAME
    # 原子写：WE 可能随时读取封面，不能让它看到半张 JPEG；
    # 中途失败也保证旧文件/兜底逻辑仍然拿到完整文件。
    temp = preview.with_name(preview.name + ".tmp")
    try:
        image.save(temp, "JPEG", quality=88, optimize=True)
        os.replace(temp, preview)
    finally:
        temp.unlink(missing_ok=True)
    return preview


def write_preview_from_data_url(
    proj, data_url: str, palette=None, bg: str = "monet", bg_color: str | None = None,
) -> Path | None:
    """把前端截取的完整立绘帧（PNG dataURL）写成 WE 封面 preview.jpg。

    预览画布只有角色（模型内容），背景是页面 CSS；这里按导出背景样式重绘
    背景再叠加角色，所以封面既完整又不依赖预览组件的当前缩放/偏移。
    """
    import io

    from PIL import Image

    if not data_url or "," not in str(data_url):
        return None
    try:
        payload = base64.b64decode(str(data_url).split(",", 1)[1])
    except Exception:  # noqa: BLE001
        return None
    if not payload.startswith(b"\x89PNG"):
        return None
    try:
        with Image.open(io.BytesIO(payload)) as raw:
            character = raw.convert("RGBA")
        background = render_cover_background(
            character.width, character.height, palette, bg, bg_color
        ).convert("RGBA")
        merged = Image.alpha_composite(background, character)
        return _save_preview(merged, Path(proj))
    except Exception as ex:  # noqa: BLE001
        print(f"[preview] 写入封面失败: {ex}", flush=True)
        return None


def generate_preview_image(
    proj, image_file=None, palette=None, bg: str = "monet",
    bg_color: str | None = None, title: str = "", subtitle: str = "",
) -> Path:
    """为壁纸项目生成兜底封面 preview.jpg（已存在则保留）。

    优先用立绘原图（静态装束）合成；Live2D 没有可离线渲染的素材时生成同色系
    渐变占位图。无论走哪条路径都保证文件落盘——project.json 引用了 preview，
    缺文件会让 WE 持续报 icon create err，绝不能留空引用。
    """
    proj = Path(proj)
    preview = proj / PREVIEW_NAME
    if preview.is_file():
        return preview
    try:
        from PIL import Image

        if image_file and Path(image_file).is_file():
            character = _open_preview_source(Path(image_file))
            character.thumbnail(_PREVIEW_MAX, Image.Resampling.LANCZOS)
            background = render_cover_background(
                character.width, character.height, palette, bg, bg_color
            ).convert("RGBA")
            _save_preview(Image.alpha_composite(background, character), proj)
        else:
            placeholder = render_cover_background(1280, 720, palette, bg, bg_color)
            _draw_cover_title(placeholder, title, subtitle)
            _save_preview(placeholder, proj)
    except Exception as ex:  # noqa: BLE001
        print(f"[preview] 生成封面失败，改用内置兜底图: {ex}", flush=True)
    if not preview.is_file():
        preview.write_bytes(_FALLBACK_PREVIEW_JPEG)
    return preview


def layout_storage_key(skin: dict, wallpaper_type: str) -> str:
    """生成稳定且按壁纸类型隔离的布局存储键。

    派生项目的元数据字段并不统一，因此兼容 character/ship/id/mbcc，
    同时纳入 bundle/painting/name。键不含每次导出的随机目录后缀，同一资源重新
    导出后仍能恢复用户在 Wallpaper Engine 中拖动的位置。
    """
    identity = "\0".join(
        str(skin.get(key) or "")
        for key in ("character", "ship", "id", "mbcc", "bundle", "painting", "name")
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    kind = str(wallpaper_type or "unknown").strip().lower() or "unknown"
    return f"path-to-nowhere-wallpaper:layout:v1:{kind}:{digest}"


TITLE_PREFIX = "无期迷途"
TITLE_SUFFIX = "Path to Nowhere"
TITLE_SEP = "│"


def wallpaper_title(character: str, name: str, tags: list[str] | None = None) -> str:
    """生成 WE 壁纸标题：`无期迷途│角色·装束│互动·语音│Path to Nowhere`。

    角色与装束、能力标签之间用 `·`，段与段之间用 `│`。tags 只列该壁纸实际
    具备的能力（静态立绘没有互动/语音，整段省略；Live2D 关掉互动或没有语音
    素材时同样不写），避免标题与实际功能不符。
    """
    who = "·".join(part for part in (str(character or ""), str(name or "")) if part)
    active = [tag for tag in (tags or []) if tag]
    parts = [TITLE_PREFIX, who]
    if active:
        parts.append("·".join(active))
    parts.append(TITLE_SUFFIX)
    return TITLE_SEP.join(part for part in parts if part)


def project_dir_name(character: str, name: str) -> str:
    """生成安全的壁纸项目目录名。

    目录名带「无期迷途」前缀但不含互动/语音后缀：we_integration 以目录名
    （去随机后缀）作为皮肤身份清理旧副本，后缀随导出开关变化会破坏清理匹配。
    每次导出追加 8 位随机后缀：Wallpaper Engine 按“目录路径”记忆属性覆盖值，
    新目录可避免旧路径覆盖导出默认值。用户后续调整的位置另由稳定的
    layout_storage_key 写入 localStorage，并在首次属性推送时优先恢复。Windows 不允许目录名以空格/点结尾，也禁止
    <>:"/\\|?* 等字符。
    """
    raw = f"{TITLE_PREFIX}_{character}_{name}"
    cleaned = "".join(ch for ch in raw if ch not in '<>:"/\\|?*').rstrip(" .")
    return f"{cleaned or 'wallpaper'}_{uuid.uuid4().hex[:8]}"


def project_identity(project_name: str) -> str:
    """项目目录名 → 皮肤身份（去掉 _uuid8 与标题前缀）。

    兼容加前缀前后的目录名：`{character}_{name}_{uuid8}` 与
    `无期迷途_{character}_{name}_{uuid8}` 都归一到同一身份，
    升级后仍能清理旧导出目录和 WE 里旧的一键应用副本。
    """
    identity = re.sub(r"_[0-9a-f]{8}$", "", project_name)
    prefix = f"{TITLE_PREFIX}_"
    if identity.startswith(prefix):
        identity = identity[len(prefix):]
    return identity


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def project_json(
    title: str,
    preview: str = PREVIEW_NAME,
    scale: int = 100,
    offset_x: int = 0,
    offset_y: int = 0,
    alignment: int = 0,
    animations: list[str] | None = None,
    animation: str = "",
    l2d: bool = False,
    track: bool = True,
    interact: bool = True,
    voice: bool = True,
    subtitle: bool = True,
    intro: bool = True,
) -> dict:
    # 语义与预览一致：scale 为百分比（100 = 自适应），偏移为画布宽/高百分比
    scale_ctl = int(_clamp(scale, 20, 800))
    ox = int(_clamp(offset_x, -100, 100))
    oy = int(_clamp(offset_y, -100, 100))

    # 采用 Wallpaper Engine 编辑器自己保存的字段格式（editable/fraction/index/order），
    # 避免 WE 导入时对滑块 value 做二次换算/误写覆盖值。
    def slider(text: str, value: int, lo: int, hi: int, order: int, index: int) -> dict:
        return {
            "text": text,
            "type": "slider",
            "value": value,
            "min": lo,
            "max": hi,
            "editable": True,
            "fraction": False,
            "index": index,
            "order": order,
        }

    def bool_ctl(text: str, value: bool, order: int, index: int) -> dict:
        return {
            "text": text,
            "type": "bool",
            "value": bool(value),
            "editable": True,
            "index": index,
            "order": order,
        }

    properties = {
        "scalectrl": slider("缩放", scale_ctl, 20, 800, 103, 3),
        "offsetx": slider("水平偏移", ox, -100, 100, 101, 1),
        "offsety": slider("垂直偏移", oy, -100, 100, 102, 2),
        "alignment": {
            "text": "对齐方式", "type": "combo",
            # WE combo 标准格式：value 是选中项的字符串值，options 是 {label,value} 对象数组。
            # 旧格式（整数索引 + 字符串数组）会导致该属性在 WE 侧边栏不显示。
            "value": (
                ALIGN_NAMES[alignment]
                if isinstance(alignment, int) and 0 <= alignment < len(ALIGN_NAMES)
                else str(alignment or "center")
            ),
            "options": [
                {"label": label, "value": name}
                for name, label in zip(ALIGN_NAMES, ALIGN_LABELS)
            ],
            # editable 必须为 true：WE 属性面板只显示 editable 的属性，
            # 漏掉该字段会导致「对齐方式」在 WE 控件里不显示
            "editable": True,
            "index": 0,
            "order": 100,
        },
    }

    properties["adjustsize"] = bool_ctl("调整位置", False, 109, 9)

    # Live2D 更新与渲染共用 app.ticker，目标帧率只在动态类型中出现。
    if l2d:
        properties["targetfps"] = slider("目标帧率", 60, 15, 200, 104, 4)

    # Live2D 专属面板开关：鼠标追踪（视线/头部跟随鼠标）。
    # 开关在导出时关闭 → 不生成对应属性：WE 右侧面板不出现该控件。
    if l2d and track:
        properties["mousetrack"] = bool_ctl("鼠标追踪", True, 108, 8)

    # Live2D 互动面板开关：预览里的四个开关同步到 WE 控件，
    # 壁纸运行时（live2d-app.js）通过 wallpaperPropertyListener 实时响应。
    if l2d:
        properties["interact"] = bool_ctl("动作互动", interact, 110, 10)
        properties["voice"] = bool_ctl("语音", voice, 111, 11)
        properties["subtitle"] = bool_ctl("台词文本", subtitle, 112, 12)
        properties["intro"] = bool_ctl("入场动画", intro, 113, 13)

    return {
        "file": "index.html",
        "general": {"properties": properties},
        "title": title,
        "type": "web",
        "preview": preview,
    }


def export_defaults(options: dict) -> dict:
    """把预览的缩放/偏移/对齐换算成壁纸 HTML 初始值（与预览语义一致）。"""
    scale = int(_clamp(int(options.get("scale", 100) or 100), 20, 800))
    ox = int(_clamp(int(options.get("offsetX", 0) or 0), -100, 100))
    oy = int(_clamp(int(options.get("offsetY", 0) or 0), -100, 100))
    align = options.get("alignment", 0)
    align_name = ALIGN_NAMES[align] if isinstance(align, int) and 0 <= align < len(ALIGN_NAMES) else str(align or "center")
    anim = options.get("animation") or ""
    return {
        "SCALE": str(scale),
        "OFFSET_X": str(ox),
        "OFFSET_Y": str(oy),
        "ALIGNMENT": align_name,
        "ANIMATION": json.dumps(str(anim), ensure_ascii=False),
    }


def render_template(name: str, **replacements) -> str:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    for key, value in replacements.items():
        text = text.replace("{{" + key + "}}", value)
    return text
