"""Live2D 壁纸导出插件：生成 WE web 壁纸项目（pixi + Cubism 运行时）。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from core.registry import ExporterPlugin
from core.wallpaper import (
    L2D_APP,
    L2D_CORE,
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


def copy_voice_assets(skin: dict, options: dict, assets_dir: Path) -> dict:
    """把当前装束的语音台词打包进壁纸工程 assets/voice/。

    返回 {has_lines, has_audio, lines}：lines 为归一化台词（audio 改写为壁纸内相对路径
    assets/voice/<动作>.mp3）；无台词数据时 lines 为空结构。
    """
    root = Path(options["root"])
    painting = str(skin.get("painting") or "")
    lines = {
        "greet": [], "boring": [], "idle": [], "level": [], "touch": [],
        "interact": [], "interact_exit": [],
    }
    has_audio = False

    voice_json = root / "resources" / "metadata" / "voice_lines.json"
    if painting and voice_json.is_file():
        from core.voice_lines import merged_voice_lines

        data = merged_voice_lines(root, painting, str(skin.get("character") or ""))
        if data:
            voice_src = root / "resources" / "voice" / painting
            voice_dst = assets_dir / "voice"
            for category, items in data.items():
                if category not in lines:
                    continue
                for item in items:
                    audio_rel = None
                    source_rel = str(item.get("audio") or "")
                    src_audio = root / "resources" / source_rel if source_rel else None
                    if src_audio is not None and src_audio.is_file():
                        voice_dst.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_audio, voice_dst / f"{item['action']}.mp3")
                        audio_rel = f"assets/voice/{item['action']}.mp3"
                        has_audio = True
                    lines[category].append({
                        "action": item.get("action", ""),
                        "text": item.get("text", ""),
                        "audio": audio_rel,
                    })
            if data.get("head_click_interact"):
                lines["head_click_interact"] = True

    has_lines = any(bool(lines.get(cat)) for cat in ("greet", "boring", "idle", "level", "touch", "interact", "interact_exit"))
    if has_lines:
        (assets_dir / "voice" / "lines.json").write_text(
            json.dumps(lines, ensure_ascii=False), encoding="utf-8"
        )
    return {"has_lines": has_lines, "has_audio": has_audio, "lines": lines}


def build_interact_seq_for_export(model_json: Path, stage2: bool = False,
                                  head_interact: bool = False) -> list[dict]:
    """按导出模型的真实动作清单生成点击互动序列。

    读取失败（模型损坏/旧格式）时返回空列表，运行时回落到自己的 JS 规则，
    不能因为互动序列算不出来就中断整次壁纸导出。
    stage2：四星皮二阶段（语音库 interact 池非空）——greet 取互动1、
    Click_Interactivearea 进点击轮换、Exitidle 播退出互动。
    head_interact：装束级例外（语音库 head_click_interact=true，目前仅画中晨曦
    二阶段）——Click_Head 不固定摸头，同样进互动轮换。
    """
    try:
        from core.interaction import build_interact_seq, motion_labels

        meta = json.loads(Path(model_json).read_text(encoding="utf-8"))
        return build_interact_seq(motion_labels(meta), stage2=stage2, head_interact=head_interact)
    except Exception:  # noqa: BLE001
        return []



class WallpaperLive2DExporter(ExporterPlugin):
    id = "wallpaper_live2d"
    name = "Live2D 壁纸导出"

    def export(self, skin: dict, options: dict, out_dir: str) -> str:
        out = Path(out_dir)
        proj = out / project_dir_name(skin["character"], skin["name"])
        model_dir = proj / "assets" / "model"
        model_dir.mkdir(parents=True, exist_ok=True)

        src_dir = Path(options["root"]) / "resources" / skin["asset"]["dir"]
        # 复制模型目录全部文件（model3.json/moc3/贴图/动作）
        for f in src_dir.rglob("*"):
            if f.is_file():
                rel = f.relative_to(src_dir)
                dst = model_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)

        upscale_version = options.get("upscaleVersion") or "none"
        model_file = skin["asset"]["model"]
        if upscale_version != "none":
            # 注意：stem 只去掉最后一个后缀（.json），会留下 ".model3"，
            # 变体文件名必须按完整后缀 ".model3.json" 截取（与 core/upscaler.py 一致）
            base_stem = model_file[: -len(".model3.json")] if model_file.endswith(".model3.json") else Path(model_file).stem
            variant = f"{base_stem}_{upscale_version}.model3.json"
            if (src_dir / variant).is_file():
                model_file = variant

        from core.upscaler import UPSCALE_TAG_RE

        for f in model_dir.rglob("*"):
            if f.is_file() and UPSCALE_TAG_RE.search(f.name):
                # 如果当前有选定版本且属于当前版本，保留；否则清理避免包体膨胀
                if upscale_version != "none" and upscale_version in f.name:
                    continue
                try:
                    f.unlink()
                except Exception:
                    pass

        shutil.copy2(L2D_CORE, proj / "live2dcubismcore.min.js")
        shutil.copy2(L2D_APP, proj / "live2d-app.js")
        shutil.copy2(WALLPAPER_LAYOUT, proj / "wallpaper-layout.js")

        # 语音台词：打包 mp3 与台词清单（无数据时运行时静默降级为纯动作互动）
        voice_meta = copy_voice_assets(skin, options, proj / "assets")

        # 互动序列：与前端/壁纸运行时同一条规则（core/interaction.py），
        # 在导出时按实际 model3.json 算好注入，运行时无需再猜动作语义。
        # 四星皮二阶段（语音库 interact 池非空）走二阶段规则：greet→互动1、
        # Click_Interactivearea 进轮换、Exitidle→退出互动。
        # head_click_interact=true（画中晨曦二阶段）：Click_Head 也进互动轮换。
        voice_lines_merged = voice_meta.get("lines", {})
        stage2 = bool(voice_lines_merged.get("interact"))
        head_interact = bool(voice_lines_merged.get("head_click_interact"))
        interact_seq = build_interact_seq_for_export(
            model_dir / model_file, stage2=stage2, head_interact=head_interact,
        )

        cfg = {"model": "assets/model/" + model_file}
        # WE 面板开关默认值：取预览侧开关状态，导出后在 WE 面板可实时切换。
        track_on = bool(options.get("track", True))
        interact_on = bool(options.get("interact", True))
        # 有开关但没打包到语音素材时运行时会静默降级，标题也不该写「语音」。
        voice_on = bool(options.get("voice", True)) and voice_meta["has_audio"]
        html = render_template(
            "wallpaper_live2d.html",
            L2D_CONFIG_JSON=json.dumps(cfg, ensure_ascii=False),
            LAYOUT_STORAGE_KEY=json.dumps(layout_storage_key(skin, "live2d"), ensure_ascii=False),
            BG_CSS=bg_css_for_skin(
                skin, options.get("root"), options.get("bg", "monet"), options.get("bgColor")
            ),
            TRACK="true" if track_on else "false",
            INTERACT="true" if interact_on else "false",
            VOICE="true" if voice_on else "false",
            SUBTITLES="true" if bool(options.get("subtitle", True)) else "false",
            INTRO="true" if bool(options.get("intro", True)) else "false",
            HAS_VOICE_LINES="true" if voice_meta["has_lines"] else "false",
            VOICE_LINES_JSON=json.dumps(voice_meta["lines"], ensure_ascii=False),
            INTERACT_SEQ_JSON=json.dumps(interact_seq, ensure_ascii=False),
            **export_defaults(options),
        )
        (proj / "index.html").write_text(html, encoding="utf-8")
        (proj / "project.json").write_text(
            json.dumps(
                project_json(
                    wallpaper_title(
                        skin["character"],
                        skin["name"],
                        tags=["互动" if interact_on else "", "语音" if voice_on else ""],
                    ),
                    scale=options.get("scale", 100),
                    offset_x=options.get("offsetX", 0),
                    offset_y=options.get("offsetY", 0),
                    alignment=options.get("alignment", 0),
                    l2d=True,
                    track=track_on,
                    interact=interact_on,
                    voice=voice_on,
                    subtitle=bool(options.get("subtitle", True)),
                    intro=bool(options.get("intro", True)),
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        # 兜底封面：正常 UI 导出会由前端提供完整立绘截图并覆盖此文件；
        # CLI/无预览上下文的导出也必须有真实 preview 文件（WE 缺图会持续报错）。
        generate_preview_image(
            proj,
            bg=options.get("bg", "monet"),
            bg_color=options.get("bgColor"),
            title=f"{skin['character']} · {skin['name']}",
        )
        return str(proj)
