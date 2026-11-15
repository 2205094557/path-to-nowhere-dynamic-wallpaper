# -*- coding: utf-8 -*-
"""AI 超分模块（支持 Waifu2x / Real-CUGAN / Real-ESRGAN NCNN Vulkan 引擎）。

针对无期迷途动态立绘（Live2D 贴图）与静态立绘整体执行超分辨率处理：
- 通过 tools/<engine>-ncnn-vulkan/<version>/<engine>.exe 调用 GPU Vulkan 引擎
- Windows 下以 CREATE_NO_WINDOW 启动子进程（无命令行黑弹窗）
- Alpha-Guard 通道保护：拆分 RGB 与 Alpha 分开处理（RGB 深度学习超分，Alpha 通道由 PIL Lanczos 放大后重组，彻底杜绝黑边与边缘杂色）
- 支持 4x->2x 超采样方案（先 4x 引擎超分再 Lanczos 缩小至 2x，贴图尺寸安全且线条极度锐利）
- 非破坏性多版本管理：原始贴图永不覆盖，生成独立命名的超分贴图与衍生 model3.json 配置
- 模型 MOC3 内部使用归一化 UV 坐标 [0.0, 1.0]，无缝兼容超分后的高分辨率贴图
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from PIL import Image

# 4x 超分会把 4096² 贴图放大到 16384²（约 2.68 亿像素），超过 Pillow 默认的
# 解压炸弹阈值（MAX_IMAGE_PIXELS=89M，2 倍即抛 DecompressionBombError）。
# output_is_valid 用它校验引擎产物，会把成功的 4x 结果误判为损坏：
# Real-ESRGAN 被误判后回退 waifu2x，waifu2x 同样被误判，最终报
# 「waifu2x 放大失败 (exit=0)」。这里读写的都是本地提取资源与本地引擎输出
# （可信数据），因此放开上限；超分产物最大也只有 16384² 量级。
Image.MAX_IMAGE_PIXELS = None

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

if getattr(sys, "frozen", False):
    BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

# 算法选项与文案
ALGORITHMS: dict[str, str] = {
    "waifu2x": "Waifu2x (极佳兼容，线条锐利)",
    "realcugan": "Real-CUGAN (二次元专属柔和)",
    "realesr": "Real-ESRGAN (精细渲染)",
}

# 匹配超分标记的正则（例如 waifu2x_2x_n0_tta_ds）
UPSCALE_TAG_RE = re.compile(
    r"(waifu2x|realcugan|realesr)_(\d+x)(?:_n(\d+))?(?:_(tta))?(?:_(ds))?", re.IGNORECASE
)


def _has_alpha(path: Path | str) -> bool:
    """源图是否带 Alpha 通道（决定超分产物是否必须带 Alpha）。"""
    try:
        with Image.open(path) as im:
            return "A" in im.getbands() or im.mode in ("LA", "PA")
    except Exception:  # noqa: BLE001
        return False


def output_is_valid(path: Path | str, require_alpha: bool = False) -> bool:
    """校验产物图片是否可用：存在、非空、且能被 PIL 解码。

    require_alpha：源图带 Alpha 时必须为 True。Alpha-Guard 会先让引擎输出
    RGB 中间文件、再合成 Alpha；若中途失败（如内存不足/旧版像素上限误判），
    磁盘上会留下无 Alpha 的残缺 PNG。这种文件若被当成有效产物复用，预览里
    透明区域会变成白色块（画面崩坏），因此必须判为无效并重新生成。
    """
    p = Path(path)
    try:
        if not p.is_file() or p.stat().st_size == 0:
            return False
        with Image.open(p) as im:
            bands = im.getbands()
            mode = im.mode
            im.verify()
        if require_alpha and "A" not in bands and mode not in ("LA", "PA"):
            return False
        return True
    except Exception:  # noqa: BLE001
        return False


def find_engine_exe(engine_name: str) -> str:
    """定位指定引擎的 exe 可执行文件路径。"""
    base = BASE_DIR / "tools" / f"{engine_name}-ncnn-vulkan"
    if not base.is_dir():
        return ""
    for child in base.iterdir():
        if child.is_dir():
            exe = child / f"{engine_name}-ncnn-vulkan.exe"
            if exe.is_file():
                return str(exe)
    return ""


def _process_with_alpha_guard(
    src_png: str,
    out_png: str,
    engine_runner,
    scale: int,
) -> str:
    """若图片带 Alpha 通道，拆分 RGB 与 Alpha 分别处理。

    RGB 走 NCNN AI 超分，Alpha 通道用 PIL Lanczos 独立放大后合成，
    避免 Vulkan 引擎直接处理半透明边缘导致的黑边与杂色伪影。
    """
    with Image.open(src_png) as im:
        has_alpha = ("A" in im.getbands()) or (im.mode in ("RGBA", "LA", "PA"))
        if not has_alpha:
            return engine_runner(src_png, out_png)

        orig_rgba = im.convert("RGBA")
        r, g, b, a = orig_rgba.split()
        rgb_im = Image.merge("RGB", (r, g, b))

    out_p = Path(out_png)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    tmp_rgb = out_p.parent / f"_tmp_rgb_{out_p.name}"
    # 引擎输出先落到临时文件：Alpha 合成失败时磁盘上不能留下“无 Alpha 但可解码”
    # 的残缺 PNG（否则会被 output_is_valid 当成有效产物复用，预览透明区变白块）。
    tmp_up = out_p.parent / f"_tmp_up_{out_p.name}"
    try:
        rgb_im.save(tmp_rgb, "PNG")
        upscaled_rgb_path = engine_runner(str(tmp_rgb), str(tmp_up))

        with Image.open(upscaled_rgb_path) as up_rgb:
            up_w, up_h = up_rgb.size
            a_resized = a.resize((up_w, up_h), Image.LANCZOS)
            up_rgb_conv = up_rgb.convert("RGBA")
            up_rgb_conv.putalpha(a_resized)
            up_rgb_conv.save(out_p, "PNG")

        return str(out_p)
    finally:
        for tmp in (tmp_rgb, tmp_up):
            if tmp.exists():
                try:
                    tmp.unlink()
                except Exception:
                    pass


def upscale_waifu2x(
    png_path: str,
    out_path: str,
    scale: int = 2,
    denoise: int = 0,
    timeout: int = 600,
    tta: bool = False,
) -> str:
    """调用 waifu2x-ncnn-vulkan 执行超分。"""
    exe = find_engine_exe("waifu2x")
    if not exe:
        raise FileNotFoundError("未找到 waifu2x-ncnn-vulkan 引擎，请检查 tools 目录")

    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    def _run(src: str, dst: str) -> str:
        d = max(0, min(3, int(denoise)))
        cmd = [exe, "-i", str(src), "-o", str(dst), "-s", str(scale), "-n", str(d), "-g", "0"]
        if tta:
            cmd.append("-x")

        result = subprocess.run(
            cmd, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW
        )
        if result.returncode != 0 or not output_is_valid(Path(dst)):
            err = result.stderr.decode("utf-8", errors="replace")[-600:] if result.stderr else ""
            raise RuntimeError(f"waifu2x 放大失败 (exit={result.returncode}): {err}")
        return str(dst)

    return _process_with_alpha_guard(png_path, out_path, _run, scale)


def upscale_realcugan(
    png_path: str,
    out_path: str,
    scale: int = 2,
    noise: int = -1,
    timeout: int = 600,
    tta: bool = False,
) -> str:
    """调用 realcugan-ncnn-vulkan 执行超分。"""
    exe = find_engine_exe("realcugan")
    if not exe:
        raise FileNotFoundError("未找到 realcugan-ncnn-vulkan 引擎，请检查 tools 目录")

    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    def _run(src: str, dst: str) -> str:
        n = max(-1, min(3, int(noise)))
        # 使用 models-se（包含 2x/3x/4x 完整权重）
        cmd = [exe, "-i", str(src), "-o", str(dst), "-m", "models-se", "-n", str(n), "-s", str(scale), "-g", "0"]
        if tta:
            cmd.append("-x")
        res = subprocess.run(
            cmd, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW
        )
        if res.returncode != 0 or not output_is_valid(Path(dst)):
            err = res.stderr.decode("utf-8", errors="replace")[-600:] if res.stderr else ""
            raise RuntimeError(f"realcugan 放大失败 (exit={res.returncode}): {err}")
        return str(dst)

    return _process_with_alpha_guard(png_path, out_path, _run, scale)


def upscale_realesr(
    png_path: str,
    out_path: str,
    scale: int = 2,
    timeout: int = 600,
    tta: bool = False,
) -> str:
    """调用 realesrgan-ncnn-vulkan 执行超分（遇驱动错误自动回退到 waifu2x）。"""
    exe = find_engine_exe("realesrgan")
    if not exe:
        raise FileNotFoundError("未找到 realesrgan-ncnn-vulkan 引擎，请检查 tools 目录")

    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    def _run(src: str, dst: str) -> str:
        model_name = "realesrgan-x4plus-anime" if scale == 4 else "realesr-animevideov3-x2"
        cmd = [exe, "-i", str(src), "-o", str(dst), "-n", model_name, "-s", str(scale), "-g", "0", "-j", "1:1:1"]
        if tta:
            cmd.append("-x")

        result = subprocess.run(
            cmd, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW
        )
        has_vk_error = result.stderr and b"vkQueueSubmit failed" in result.stderr
        if result.returncode != 0 or not output_is_valid(Path(dst)) or has_vk_error:
            # 显卡兼容异常自动回退到 waifu2x
            if Path(dst).is_file():
                try:
                    Path(dst).unlink()
                except Exception:
                    pass
            return upscale_waifu2x(src, dst, scale=scale, denoise=0, timeout=timeout, tta=tta)
        return str(dst)

    return _process_with_alpha_guard(png_path, out_path, _run, scale)


def upscale_image(
    src_path: str | Path,
    out_path: str | Path,
    scale: int = 2,
    algorithm: str = "waifu2x",
    denoise: int = 0,
    tta: bool = False,
    downsample: bool = False,
    timeout: int = 600,
) -> str:
    """统一单张图片超分入口，支持 4x->2x 超采样。"""
    src_p = Path(src_path)
    out_p = Path(out_path)
    if not src_p.is_file():
        raise FileNotFoundError(f"源图片不存在: {src_p}")

    if scale not in (2, 4):
        scale = 2
    if algorithm not in ALGORITHMS:
        algorithm = "waifu2x"

    # 若启用 4x->2x 超采样
    run_scale = 4 if (scale == 2 and downsample) else scale
    intermediate_file = str(out_p) if run_scale == scale else str(out_p.parent / f"_tmp_{out_p.name}")

    try:
        if algorithm == "realcugan":
            cugan_noise = -1 if denoise <= 0 else (denoise - 1)
            out = upscale_realcugan(str(src_p), intermediate_file, scale=run_scale, noise=cugan_noise, tta=tta, timeout=timeout)
        elif algorithm == "realesr":
            out = upscale_realesr(str(src_p), intermediate_file, scale=run_scale, tta=tta, timeout=timeout)
        else:
            out = upscale_waifu2x(str(src_p), intermediate_file, scale=run_scale, denoise=denoise, tta=tta, timeout=timeout)

        # 若是 4x->2x 超采样，进行高质量 Lanczos 缩小
        if run_scale == 4 and scale == 2 and downsample:
            with Image.open(out) as im:
                w2 = im.width // 2
                h2 = im.height // 2
                ds_im = im.resize((w2, h2), Image.LANCZOS)
                ds_im.save(out_p, "PNG")
            if Path(out).exists():
                try:
                    Path(out).unlink()
                except Exception:
                    pass
            out = str(out_p)

        final_p = Path(out)
        if not output_is_valid(final_p, require_alpha=_has_alpha(src_p)):
            if final_p.is_file():
                try:
                    final_p.unlink()
                except Exception:
                    pass
            raise RuntimeError("超分输出图片异常（文件缺失或损坏）")
        return str(final_p)
    finally:
        tmp_inter = out_p.parent / f"_tmp_{out_p.name}"
        if tmp_inter.exists():
            try:
                tmp_inter.unlink()
            except Exception:
                pass


def make_upscale_tag(
    algorithm: str, scale: int, denoise: int = 0, tta: bool = False, downsample: bool = False
) -> str:
    """生成唯一的超分版本标识串，例如 waifu2x_2x, realcugan_4x_n1_tta。"""
    parts = [algorithm, f"{scale}x"]
    if denoise != 0:
        parts.append(f"n{denoise}")
    if tta:
        parts.append("tta")
    if downsample and scale == 2:
        parts.append("ds")
    return "_".join(parts)


def format_upscale_label(tag: str) -> str:
    """把超分标记格式化为人类可读的选项名称。"""
    m = UPSCALE_TAG_RE.search(tag)
    if not m:
        return tag
    algo, scale_str, denoise, tta, ds = m.groups()
    algo_name = algo.capitalize()
    if algo == "waifu2x":
        algo_name = "Waifu2x"
    elif algo == "realcugan":
        algo_name = "Real-CUGAN"
    elif algo == "realesr":
        algo_name = "Real-ESRGAN"

    parts = [f"{algo_name} {scale_str}"]
    extras = []
    if denoise:
        extras.append(f"降噪{denoise}")
    if tta:
        extras.append("TTA")
    if ds:
        extras.append("4x超采样")
    if extras:
        parts.append(f"({', '.join(extras)})")
    return " ".join(parts)


def upscale_skin_asset(
    skin_type: str,
    asset_dir: str | Path,
    scale: int = 2,
    algorithm: str = "waifu2x",
    denoise: int = 0,
    tta: bool = False,
    downsample: bool = False,
) -> dict:
    """对已提取的装束资源（Live2D 贴图或静态立绘）执行超分，生成非覆盖式版本。"""
    root_dir = BASE_DIR / "resources" / asset_dir
    if not root_dir.is_dir():
        return {"ok": False, "error": f"资源目录不存在: {asset_dir}"}

    tag = make_upscale_tag(algorithm, scale, denoise=denoise, tta=tta, downsample=downsample)
    label = format_upscale_label(tag)

    if skin_type == "live2d":
        # 查找原始 model3.json：标准命名 *.model3.json 优先；PTN 组装产物固定叫
        # model3.json（tools/assemble_live2d.py），作为回退。
        model_jsons = [f for f in root_dir.glob("*.model3.json") if not UPSCALE_TAG_RE.search(f.name)]
        if model_jsons:
            orig_model_json = model_jsons[0]
        elif (root_dir / "model3.json").is_file():
            orig_model_json = root_dir / "model3.json"
        else:
            return {"ok": False, "error": "未找到原始 .model3.json 文件"}
        # 变体名按完整后缀截取：model3.json 的 stem 是 "model3"，
        # 生成 model3_<tag>.model3.json，与 *.model3.json 扫描/删除逻辑兼容
        if orig_model_json.name.endswith(".model3.json"):
            model_stem = orig_model_json.name[: -len(".model3.json")]
        else:
            model_stem = orig_model_json.name[: -len(".json")]

        try:
            with open(orig_model_json, "r", encoding="utf-8") as f:
                model_data = json.load(f)
        except Exception as e:
            return {"ok": False, "error": f"读取 .model3.json 失败: {e}"}

        orig_texture_rels = model_data.get("FileReferences", {}).get("Textures", [])
        if not orig_texture_rels:
            return {"ok": False, "error": "模型未声明贴图纹理"}

        new_texture_rels: list[str] = []
        texture_details = []
        target_res = ""

        for rel in orig_texture_rels:
            base_tex_path = root_dir / rel
            if not base_tex_path.is_file():
                return {"ok": False, "error": f"贴图文件不存在: {rel}"}

            # 目标贴图路径：例如 textures/texture_00_waifu2x_2x.png
            dest_tex_name = f"{base_tex_path.stem}_{tag}.png"
            dest_tex_path = base_tex_path.parent / dest_tex_name
            rel_dest = str(Path(rel).parent / dest_tex_name).replace("\\", "/")

            if not output_is_valid(dest_tex_path, require_alpha=_has_alpha(base_tex_path)):
                upscale_image(
                    base_tex_path,
                    dest_tex_path,
                    scale=scale,
                    algorithm=algorithm,
                    denoise=denoise,
                    tta=tta,
                    downsample=downsample,
                )

            with Image.open(dest_tex_path) as up_im:
                target_res = f"{up_im.width}×{up_im.height}"

            new_texture_rels.append(rel_dest)
            texture_details.append({"name": dest_tex_name, "resolution": target_res})

        # 写入衍生 model3.json：例如 Adela_2_waifu2x_2x.model3.json
        variant_model_name = f"{model_stem}_{tag}.model3.json"
        variant_model_path = root_dir / variant_model_name
        model_data["FileReferences"]["Textures"] = new_texture_rels
        with open(variant_model_path, "w", encoding="utf-8") as f:
            json.dump(model_data, f, ensure_ascii=False, indent=2)

        return {
            "ok": True,
            "version": tag,
            "label": label,
            "model": variant_model_name,
            "textures": texture_details,
            "resolution": target_res,
            "message": f"超分完成：贴图已放大至 {target_res}",
        }

    elif skin_type == "static":
        base_img = root_dir / "painting.png"
        if not base_img.is_file():
            return {"ok": False, "error": f"未找到立绘原图: {base_img}"}

        dest_name = f"painting_{tag}.png"
        dest_path = root_dir / dest_name
        if not output_is_valid(dest_path, require_alpha=_has_alpha(base_img)):
            upscale_image(
                base_img,
                dest_path,
                scale=scale,
                algorithm=algorithm,
                denoise=denoise,
                tta=tta,
                downsample=downsample,
            )

        with Image.open(dest_path) as up_im:
            target_res = f"{up_im.width}×{up_im.height}"

        return {
            "ok": True,
            "version": tag,
            "label": label,
            "image": dest_name,
            "resolution": target_res,
            "message": f"超分完成：立绘已放大至 {target_res}",
        }

    return {"ok": False, "error": f"不支持的装束类型: {skin_type}"}


def get_skin_upscale_versions(skin_type: str, asset_dir: str | Path) -> dict:
    """查询指定装束已有的超分版本列表与基础分辨率。"""
    root_dir = BASE_DIR / "resources" / asset_dir
    if not root_dir.is_dir():
        return {"ok": True, "versions": [], "base_resolution": ""}

    versions = []
    base_res = ""

    if skin_type == "live2d":
        # 获取基础原图分辨率
        base_tex = root_dir / "textures" / "texture_00.png"
        if base_tex.is_file():
            try:
                with Image.open(base_tex) as im:
                    base_res = f"{im.width}×{im.height}"
            except Exception:
                pass

        # 扫描衍生 .model3.json
        for m_file in root_dir.glob("*.model3.json"):
            match = UPSCALE_TAG_RE.search(m_file.name)
            if not match:
                continue
            tag = match.group(0)
            # 获取超分贴图分辨率
            res_str = ""
            try:
                with open(m_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                texs = data.get("FileReferences", {}).get("Textures", [])
                if texs:
                    tex_file = root_dir / texs[0]
                    if tex_file.is_file():
                        with Image.open(tex_file) as im:
                            res_str = f"{im.width}×{im.height}"
            except Exception:
                pass

            label = format_upscale_label(tag)
            if res_str:
                label += f" ({res_str})"

            versions.append(
                {
                    "id": tag,
                    "label": label,
                    "model": m_file.name,
                    "resolution": res_str,
                }
            )

    elif skin_type == "static":
        base_img = root_dir / "painting.png"
        if base_img.is_file():
            try:
                with Image.open(base_img) as im:
                    base_res = f"{im.width}×{im.height}"
            except Exception:
                pass

        for p_file in root_dir.glob("painting_*.png"):
            match = UPSCALE_TAG_RE.search(p_file.name)
            if not match:
                continue
            tag = match.group(0)
            res_str = ""
            try:
                with Image.open(p_file) as im:
                    res_str = f"{im.width}×{im.height}"
            except Exception:
                pass

            label = format_upscale_label(tag)
            if res_str:
                label += f" ({res_str})"

            versions.append(
                {
                    "id": tag,
                    "label": label,
                    "image": p_file.name,
                    "resolution": res_str,
                }
            )

    return {
        "ok": True,
        "versions": versions,
        "base_resolution": base_res,
        "algorithms": ALGORITHMS,
    }


def delete_skin_upscale_version(
    skin_type: str, asset_dir: str | Path, version: str
) -> dict:
    """清理指定的超分衍生版本。"""
    root_dir = BASE_DIR / "resources" / asset_dir
    if not root_dir.is_dir():
        return {"ok": False, "error": f"资源目录不存在: {asset_dir}"}

    deleted_count = 0
    if skin_type == "live2d":
        # 删除对应的 .model3.json 与纹理
        for m_file in root_dir.glob(f"*_{version}.model3.json"):
            try:
                with open(m_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for tex_rel in data.get("FileReferences", {}).get("Textures", []):
                    tex_file = root_dir / tex_rel
                    if tex_file.is_file() and version in tex_file.name:
                        tex_file.unlink()
                        deleted_count += 1
            except Exception:
                pass
            try:
                m_file.unlink()
                deleted_count += 1
            except Exception:
                pass

    elif skin_type == "static":
        target = root_dir / f"painting_{version}.png"
        if target.is_file():
            target.unlink()
            deleted_count += 1

    return {"ok": True, "deleted": deleted_count}
