"""本地后端 API 服务：资源下载/提取/导出/取色等接口，供前端（浏览器/桌面）调用。

启动: python backend_server.py [端口]   （默认 8766）

M0 里程碑说明：本项目为无期迷途（Path to Nowhere）壁纸工具的空壳骨架——
后端可启动、路由齐备，但资源来源插件（plugins/sources）尚未注册，
下载接口统一返回 501 占位错误；元数据同步指向 tools/build_ptn_metadata.py
占位脚本（由后续里程碑提供）。
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.config import Config  # noqa: E402
from core.library import Library  # noqa: E402
from core.locks import named_lock  # noqa: E402
from core.metadata import Metadata  # noqa: E402
from core.palette import extract_palette, fallback_palette, find_palette_source, mode_css  # noqa: E402
from core.registry import Registry  # noqa: E402
from core.wallpaper import bg_css, project_identity  # noqa: E402
from core.we_integration import apply_wallpaper  # noqa: E402
from core.applog import setup_logging  # noqa: E402
from core.upscaler import (  # noqa: E402
    ALGORITHMS as UPSCALE_ALGORITHMS,
    delete_skin_upscale_version,
    get_skin_upscale_versions,
    upscale_skin_asset,
)

registry = Registry(ROOT / "plugins").discover()
metadata = Metadata(ROOT / "resources" / "metadata")
library = Library(ROOT / "resources" / "library.db")
config = Config(ROOT / "resources" / "config.json")
logger = setup_logging()

# ---- 下载阶段事件推送（SSE）：前端右下角卡片实时显示 下载/提取/同步 等阶段 ----
_EVENT_QUEUES: list[queue.Queue] = []
_EVENT_LOCK = threading.Lock()


def _broadcast(name: str, data: dict) -> None:
    payload = f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with _EVENT_LOCK:
        for q in _EVENT_QUEUES:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass


def _emit_stage(stage: str, **extra) -> None:
    _broadcast("stage", {"stage": stage, **extra})

_META_MTIMES: dict[str, float] = {}
# 批量下载取消：前端带 download_id 发起下载，点取消时置位对应 Event，
# 来源插件在下载循环中按块检查该事件，能快速停止正在进行的下载。
_CANCEL_LOCK = threading.Lock()
_CANCEL_EVENTS: dict[str, threading.Event] = {}


def _register_cancel(download_id: str) -> threading.Event:
    ev = threading.Event()
    with _CANCEL_LOCK:
        _CANCEL_EVENTS[download_id] = ev
    return ev


def _unregister_cancel(download_id: str) -> None:
    with _CANCEL_LOCK:
        _CANCEL_EVENTS.pop(download_id, None)


def cancel_download(download_id: str) -> bool:
    with _CANCEL_LOCK:
        ev = _CANCEL_EVENTS.get(download_id)
    if ev is not None:
        ev.set()
        return True
    return False

# 本地索引与提取产物是共享文件，并发下载时多线程同时写会损坏/丢数据，必须串行化。
# 用进程内命名锁（打包版模块可能多实例，threading.Lock 不共享）。
_INDEX_LOCK = named_lock("ptn_local_index")
# run_tool 与 live2d 提取器都会改写全局 sys.argv，必须共用同一把锁（与提取器同名）。
_TOOL_LOCK = named_lock("ptn_sysargv")

# 元数据构建/同步占位脚本：由后续里程碑提供（BWiki 图鉴同步 + CDN 版本检查）。
METADATA_TOOL = "build_ptn_metadata"


def ensure_fresh_metadata() -> None:
    """每个请求前调用：元数据 JSON 被外部工具/脚本改动（mtime 变化）时自动重载，
    保证界面看到的数据和后端 API（下载/导出/取色）永远一致。"""
    changed = False
    for name in ("characters.json", "skins.json", "local_skins.json"):
        p = ROOT / "resources" / "metadata" / name
        mt = p.stat().st_mtime if p.exists() else 0.0
        if _META_MTIMES.get(name) != mt:
            _META_MTIMES[name] = mt
            changed = True
    if changed:
        metadata.reload()
        refresh_library()


def refresh_library() -> None:
    library.import_metadata(metadata.characters(), metadata.skins(), metadata.local_skins())


refresh_library()


def get_config() -> dict:
    """返回当前配置（含代理设置）。"""
    return {"ok": True, "config": config.data}


def set_config(patch: dict) -> dict:
    """更新配置并立即生效（代理 / 退出清理开关）。"""
    patch = patch or {}
    if "proxy" in patch:
        config.set("proxy", (patch.get("proxy") or "").strip())
    if "clean_bundles_on_exit" in patch:
        config.set("clean_bundles_on_exit", bool(patch.get("clean_bundles_on_exit")))
    return {"ok": True, "config": config.data}


def resolve_skin(character: str, bundle: str, name: str | None = None) -> dict | None:
    # bundle 在元数据里可能落盘为 null，API 侧统一为 ""，两侧归一化后再比较
    want_bundle = bundle or ""
    hits = [
        s for s in metadata.skins()
        if s.get("character") == character and (s.get("bundle") or "") == want_bundle
    ]
    if not hits:
        return None
    if name is None:
        return hits[0]
    for s in hits:
        if s.get("name") == name:
            return s
    # 名字对不上（占位名/旧名）：仅当 character+bundle 唯一候选时容忍，
    # 避免“同 key 多装束”误配。
    return hits[0] if len(hits) == 1 else None


def downloaded_with_painting() -> list[dict]:
    out = []
    for s in library.downloaded_skins():
        skin = resolve_skin(s["character"], s["bundle"], s.get("name"))
        out.append({**s, "painting": (skin or {}).get("painting", "")})
    return out


def skin_files(skin: dict) -> list[Path]:
    """返回该装束对应的本地 bundle 与提取产物路径（generic：live2d / static）。"""
    painting = (skin.get("painting") or "").lower()
    stype = skin.get("type", "")
    b = ROOT / "resources" / "bundles"
    e = ROOT / "resources" / "extracted"
    paths: list[Path] = []
    if stype == "live2d":
        paths += [b / "live2d" / painting, e / "live2d" / painting]
    elif stype == "static":
        paths += [b / "static" / painting, e / "static" / painting]
    return [p for p in paths if p.exists()]


def delete_skin_files(character: str, bundle: str, name: str | None = None) -> dict:
    skin = resolve_skin(character, bundle, name)
    if not skin:
        return {"ok": False, "error": "装束不存在"}
    removed = [str(p) for p in skin_files(skin)]
    for p in skin_files(skin):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.is_file():
            p.unlink(missing_ok=True)
    run_tool("build_local_index", timeout=120)
    metadata.reload()
    refresh_library()
    return {"ok": True, "removed": removed}


def delete_skins_batch(items: list[dict]) -> dict:
    """批量删除装束：只删文件，最后重建一次索引。

    逐个调用 delete_skin_files 时每个装束都会触发一次 build_local_index
    （全量扫描 extracted 目录），全选删除 N 个装束就是 N 次全量重建。
    批量版把索引重建从 O(N) 降到 1 次。
    """
    removed: list[str] = []
    failed: list[dict] = []
    for it in items or []:
        try:
            character = it.get("character", "")
            bundle = it.get("bundle", "")
            name = it.get("name")
            skin = resolve_skin(character, bundle, name)
            if not skin:
                failed.append({"character": character, "bundle": bundle, "name": name, "error": "装束不存在"})
                continue
            for p in skin_files(skin):
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                elif p.is_file():
                    p.unlink(missing_ok=True)
                removed.append(str(p))
        except Exception as e:  # noqa: BLE001
            failed.append({"character": it.get("character"), "error": str(e)})

    # 统一重建一次索引，保证索引与内存一致
    try:
        run_tool("build_local_index", timeout=120)
        metadata.reload()
        refresh_library()
    except Exception as e:  # noqa: BLE001
        print(f"[batch-delete] 索引重建失败: {e}")
    return {
        "ok": not failed,
        "removed_count": len(removed),
        "failed": failed,
        "failed_count": len(failed),
    }


def clear_all_downloads() -> dict:
    removed = []
    for sub in ("bundles", "extracted"):
        d = ROOT / "resources" / sub
        if d.is_dir():
            for child in d.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
                removed.append(str(child))
    run_tool("build_local_index", timeout=120)
    metadata.reload()
    refresh_library()
    return {"ok": True, "removed": removed}


def _bundle_paths(stype: str, painting: str) -> list[Path]:
    """该装束（部件）的原始下载包路径。

    只返回本装束自身的 bundle 文件；来源插件管理的共享资源
    永远不在清理范围里。目录布局按 generic 约定 bundles/{type}/{painting}。
    """
    b = ROOT / "resources" / "bundles"
    painting = painting.lower()
    paths: list[Path] = []
    if stype == "live2d":
        paths += [b / "live2d" / painting]
    elif stype == "static":
        paths += [b / "static" / painting, b / "static" / f"{painting}_tex"]
        paths += sorted(b.glob(f"static/{painting}_*_tex"))
    return [p for p in paths if p.exists()]


def cleanup_processed_bundles() -> dict:
    """关闭软件时自动清理「已提取完成」装束的原始下载包（bundle）。

    下载包只在 下载→提取 阶段使用；提取产物（resources/extracted）才是
    预览/导出/应用的数据源，删除下载包不影响任何已提取装束。
    遍历 local_skins.json（提取成功的事实来源，build_local_index 只收录
    产物存在的装束）逐个删除其 bundle；未提取/提取失败的 bundle 不在索引里，
    自然保留，方便下次重试。
    受配置 clean_bundles_on_exit 控制，可在设置页关闭。
    """
    if not config.get("clean_bundles_on_exit", True):
        return {"ok": True, "skipped": True, "removed": [], "kept": 0}
    removed: list[str] = []
    for loc in metadata.local_skins():
        stype = loc.get("type", "")
        painting = (loc.get("painting") or "").lower()
        if not painting or stype not in ("live2d", "static"):
            continue
        for p in _bundle_paths(stype, painting):
            try:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
                removed.append(str(p))
            except Exception as e:  # noqa: BLE001
                print(f"[cleanup] 删除失败 {p}: {e}")
    if removed:
        print(f"[cleanup] 退出清理：删除 {len(removed)} 个已提取装束的下载包（保留提取产物）", flush=True)
    return {"ok": True, "removed": removed, "kept": 0}


def _read_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _thread_scoped_stream(real, tid: int, buf):
    """把 sys.stdout/stderr 换成“按线程分流”的对象：工具线程的 print 进 StringIO，
    其他线程（并发 API 请求）的输出仍落真实流。

    contextlib.redirect_stdout 是进程级替换，长工具（元数据同步）运行期间
    其他请求线程的 [api] 日志会被一并吸进工具缓冲区，混进响应里的 steps.output。
    """
    class _Scoped:
        def write(self, s):
            if threading.get_ident() == tid:
                buf.write(s)
            else:
                try:
                    real.write(s)
                except Exception:  # noqa: BLE001
                    pass
            return len(s)

        def flush(self):
            if threading.get_ident() != tid:
                try:
                    real.flush()
                except Exception:  # noqa: BLE001
                    pass

        def isatty(self):
            return False

        def reconfigure(self, **kwargs):
            # 工具脚本常在入口调用 sys.stdout.reconfigure(...)；分流对象透传为 no-op，
            # 避免工具在进程内执行时因流缺少该方法而崩溃
            pass

        @property
        def encoding(self):
            return getattr(real, "encoding", None)

    return _Scoped()


def run_tool(name: str, args: list[str] | None = None, timeout: int = 600) -> tuple[int, bytes, bytes]:
    """进程内运行 tools/ 下的脚本模块（打包后无法再用 sys.executable 起子进程）。

    tools 模块没有 __init__.py，按文件路径动态加载，避免引入包结构改动。
    注意：进程内执行意味着 timeout 只是约定上限，无法真正中断卡死的工具；
    工具自身的网络超时/重试才是兜底。
    """
    with _TOOL_LOCK:
        import importlib.util
        import io

        path = ROOT / "tools" / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"_pack_tool_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        old_argv = sys.argv
        sys.argv = [name] + (args or [])
        buf_out = io.StringIO()
        buf_err = io.StringIO()
        real_out, real_err = sys.stdout, sys.stderr
        tid = threading.get_ident()
        try:
            sys.stdout = _thread_scoped_stream(real_out, tid, buf_out)
            sys.stderr = _thread_scoped_stream(real_err, tid, buf_err)
            try:
                rc = mod.main() or 0
            finally:
                sys.stdout = real_out
                sys.stderr = real_err
        finally:
            sys.argv = old_argv
        return (
            rc,
            buf_out.getvalue().encode("utf-8", "ignore"),
            buf_err.getvalue().encode("utf-8", "ignore"),
        )


def finish_metadata_update() -> None:
    """同步完成后重建本地索引并刷新内存元数据/资源库。"""
    run_tool("build_local_index", timeout=120)
    metadata.reload()
    refresh_library()


def run_metadata_update() -> dict:
    """元数据更新占位：调用 tools/build_ptn_metadata.py。

    该脚本由后续里程碑提供（BWiki 图鉴同步 + CDN 版本检查）；
    脚本尚不存在时返回提示信息，不视为崩溃。
    """
    report: dict = {"ok": True, "steps": {}}
    _emit_stage("正在检查元数据构建脚本…")
    tool_path = ROOT / "tools" / f"{METADATA_TOOL}.py"
    if not tool_path.exists():
        report["ok"] = False
        report["steps"]["build"] = {
            "ok": False,
            "output": "",
            "error": f"tools/{METADATA_TOOL}.py 尚未创建：元数据同步暂不可用（占位，等待后续里程碑）",
        }
        _emit_stage("元数据构建脚本缺失")
        return report
    _run_update_step(report, "build", METADATA_TOOL, [], "正在构建元数据…")
    _emit_stage("正在重建本地索引…")
    try:
        finish_metadata_update()
    except Exception as e:  # noqa: BLE001
        report["steps"]["rebuild"] = {
            "ok": False,
            "output": "",
            "error": f"{type(e).__name__}: {e}"[:800],
        }
        report["ok"] = False
    _emit_stage("元数据更新完成" if report["ok"] else "元数据更新失败")
    return report


def run_wiki_sync() -> dict:
    """BWiki 图鉴同步占位：与 run_metadata_update 一致，调用 tools/build_ptn_metadata.py。"""
    return run_metadata_update()


def _run_update_step(report: dict, key: str, tool: str, args: list[str], stage: str) -> None:
    """跑一个元数据更新步骤：先推 SSE 阶段事件（前端按钮下方实时显示），
    异常也收敛进步骤记录，不让单步崩溃把整个请求打成 500/无响应。"""
    _emit_stage(stage)
    try:
        rc, out, err = run_tool(tool, args)
    except Exception as e:  # noqa: BLE001
        rc, out, err = -1, b"", f"{type(e).__name__}: {e}".encode("utf-8", "replace")
    report["steps"][key] = {
        "ok": rc == 0,
        "output": out.decode("utf-8", "ignore")[-3000:],
        "error": err.decode("utf-8", "ignore")[-800:],
    }
    if not report["steps"][key]["ok"]:
        report["ok"] = False


def local_skin_asset(character: str, bundle: str, name: str | None = None) -> dict | None:
    hits = [
        loc for loc in metadata.local_skins()
        if loc.get("character") == character and loc.get("bundle") == bundle
    ]
    if not hits:
        return None
    if name is None:
        return hits[0].get("asset")
    for loc in hits:
        if loc.get("name") == name:
            return loc.get("asset")
    # 短名回退：本地条目 name 为完整资源名（lihui_<角色>_<皮肤>），
    # 前端合成装束用短皮肤键（default/zhanzi 等），按 "_<短名>" 后缀唯一匹配
    suffix_hits = [
        loc for loc in hits
        if loc.get("name", "").lower().endswith("_" + (name or "").lower())
    ]
    if len(suffix_hits) == 1:
        return suffix_hits[0].get("asset")
    return hits[0].get("asset") if len(hits) == 1 else None


# ---- 语音台词（BWiki「语音:」页面同步产物）----

def load_voice_lines() -> dict:
    path = ROOT / "resources" / "metadata" / "voice_lines.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("skins", {})
    except Exception:  # noqa: BLE001
        return {}


def get_voice_lines(character: str, bundle: str, name: str | None) -> dict:
    skin = resolve_skin(character, bundle, name)
    if not skin:
        return {"ok": False, "error": "装束不存在"}
    painting = str(skin.get("painting") or "")
    from core.voice_lines import merged_voice_lines

    lines = merged_voice_lines(ROOT, painting, character)
    if not lines:
        return {"ok": True, "painting": painting, "has": False, "lines": None}
    return {"ok": True, "painting": painting, "has": True, "lines": lines}


def run_voice_sync(characters: list[str]) -> dict:
    """同步 BWiki 语音台词（进程内运行 tools/sync_voice_lines.py）。

    全量 241 装束 × 2s/请求 约 10 分钟；指定角色则按 body.characters 过滤。
    """
    args = [str(c) for c in characters if c]
    _emit_stage("正在同步语音台词", detail=", ".join(args) if args else "全量（较慢）")
    report: dict = {"ok": True, "steps": {}}
    try:
        rc, out, err = run_tool("sync_voice_lines", args or None, timeout=1800)
    except Exception as e:  # noqa: BLE001
        rc, out, err = -1, b"", f"{type(e).__name__}: {e}".encode("utf-8", "replace")
    report["steps"]["voice_lines"] = {
        "ok": rc == 0,
        "output": out.decode("utf-8", "ignore")[-3000:],
        "error": err.decode("utf-8", "ignore")[-800:],
    }
    if rc != 0:
        report["ok"] = False
    _emit_stage("done", ok=report["ok"], detail="语音台词同步完成" if report["ok"] else "语音台词同步失败")
    return report


ALIGN_MAP = {
    "center": 0,
    "left-top": 1,
    "right-top": 2,
    "left-bottom": 3,
    "right-bottom": 4,
}


def _export_project(character: str, bundle: str, name: str | None, options: dict) -> str:
    """生成 WE 壁纸项目，返回项目目录。"""
    skin = resolve_skin(character, bundle, name)
    if not skin:
        raise ValueError(f"装束不存在: {character}/{bundle}")
    asset = local_skin_asset(character, bundle, name)
    if not asset:
        raise ValueError("该装束尚未下载，请先下载后再导出")
    stype = skin.get("type", "")
    if stype not in ("live2d", "static"):
        raise ValueError(f"装束类型 {stype} 不支持导出壁纸")
    exporter = registry.get(
        "exporters",
        {
            "live2d": "wallpaper_live2d",
            "static": "wallpaper_static",
        }[stype],
    )
    if exporter is None:
        raise ValueError("导出插件未注册")
    opts = options or {}
    try:
        proj = exporter.export(
            {**skin, "asset": asset},
            {
                "root": str(ROOT),
                "bg": opts.get("bg", "monet"),
                "bgColor": opts.get("bgColor"),
                "scale": int(opts.get("scale", 100) or 100),
                "offsetX": int(opts.get("offsetX", 0) or 0),
                "offsetY": int(opts.get("offsetY", 0) or 0),
                "alignment": ALIGN_MAP.get(str(opts.get("alignment", "center")), 0),
                "animation": opts.get("animation", ""),
                "animations": opts.get("animations") or [],
                "track": bool(opts.get("track", True)),
                "interact": bool(opts.get("interact", True)),
                "voice": bool(opts.get("voice", True)),
                "subtitle": bool(opts.get("subtitle", True)),
                "intro": bool(opts.get("intro", True)),
                "upscaleVersion": opts.get("upscaleVersion") or "none",
            },
            str(ROOT / "resources" / "wallpapers"),
        )
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"导出失败: {e}") from e
    # 封面：前端截取的完整立绘帧优先写成 preview.jpg（背景由后端按导出样式重绘），
    # 保证 WE 封面就是完整立绘。失败时保留导出器生成的兜底封面，绝不留缺失引用。
    preview_data = opts.get("previewImage")
    if preview_data:
        try:
            from core.wallpaper import preview_palette, write_preview_from_data_url

            colors = [
                color for color in (opts.get("previewColors") or [])
                if isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color)
            ]
            palette = colors or preview_palette({**skin, "asset": asset}, ROOT)
            write_preview_from_data_url(
                Path(proj),
                str(preview_data),
                palette=palette,
                bg=opts.get("bg", "monet"),
                bg_color=opts.get("bgColor"),
            )
        except Exception as ex:  # noqa: BLE001
            print(f"[preview] 写入封面失败: {ex}", flush=True)
    return proj


def export_skin(character: str, bundle: str, name: str | None, options: dict) -> dict:
    """生成 WE 壁纸项目并打开所在文件夹（手动拖入 Wallpaper Engine）。"""
    try:
        proj = _export_project(character, bundle, name, options)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    try:
        os.startfile(str(Path(proj)))  # noqa: S606
        opened = True
    except Exception as e:  # noqa: BLE001
        opened = False
        return {
            "ok": True,
            "project": proj,
            "opened": False,
            "error": f"导出成功但打开文件夹失败: {e}",
        }
    return {
        "ok": True,
        "project": proj,
        "opened": opened,
        "message": "导出完成，已打开项目文件夹，请把 index.html 拖入 Wallpaper Engine",
    }


def export_static_image(
    character: str, bundle: str, name: str | None = None, upscale_version: str | None = None
) -> dict:
    """把静态立绘原图导出为普通图片到 resources/exports/ 并打开文件夹。"""
    skin = resolve_skin(character, bundle, name)
    if not skin:
        return {"ok": False, "error": f"装束不存在: {character}/{bundle}"}
    if skin.get("type") != "static":
        return {"ok": False, "error": "仅静态立绘支持导出图片"}
    asset = local_skin_asset(character, bundle, name)
    if not asset:
        return {"ok": False, "error": "该装束尚未下载，请先下载"}
    src_dir = ROOT / "resources" / asset["dir"]
    src = src_dir / asset.get("image", "painting.png")
    if upscale_version and upscale_version != "none":
        up_src = src_dir / f"painting_{upscale_version}.png"
        if up_src.is_file():
            src = up_src
    if not src.exists():
        return {"ok": False, "error": f"未找到立绘文件: {src}"}
    out_dir = ROOT / "resources" / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    clean_title = "".join(ch for ch in f"{character}_{skin.get('name', '')}" if ch not in '<>:"/\\|?*').rstrip(" .")
    suffix = f"_{upscale_version}" if upscale_version and upscale_version != "none" else ""
    dest = out_dir / f"{clean_title or 'painting'}{suffix}.png"
    shutil.copy2(src, dest)
    try:
        os.startfile(str(out_dir))  # noqa: S606
        opened = True
    except Exception:  # noqa: BLE001
        opened = False
    return {
        "ok": True,
        "path": str(dest),
        "opened": opened,
        "message": f"图片已导出：{dest.name}",
    }


def export_image_data(
    character: str, bundle: str, name: str | None, data_url: str, index: int | None = None
) -> dict:
    """把前端截取的当前预览帧（PNG dataURL）保存为图片并打开文件夹。"""
    import base64

    if not data_url or "," not in data_url:
        return {"ok": False, "error": "缺少图像数据"}
    try:
        png = base64.b64decode(data_url.split(",", 1)[1])
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "图像数据解析失败"}
    if not png.startswith(b"\x89PNG"):
        return {"ok": False, "error": "仅支持 PNG 截图"}
    out_dir = ROOT / "resources" / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    if index:
        safe_character = "".join(ch for ch in character if ch not in '<>:"/\\|?*').rstrip(" .")
        clean = f"{safe_character or 'character'}-{int(index)}"
    else:
        clean = "".join(ch for ch in f"{character}_{name or bundle}" if ch not in '<>:"/\\|?*').rstrip(" .")
    dest = out_dir / f"{clean or 'capture'}.png"
    dest.write_bytes(png)
    try:
        os.startfile(str(out_dir))  # noqa: S606
        opened = True
    except Exception:  # noqa: BLE001
        opened = False
    return {"ok": True, "path": str(dest), "opened": opened, "message": f"图片已导出：{dest.name}"}


def _cleanup_stale_exports(keep: str) -> int:
    """清理 resources/wallpapers 下与本次导出同角色同装束的旧项目目录。

    每次「导出并应用」成功后，删除同名壁纸（归一化身份相同）的旧导出——
    resources/wallpapers 是待应用的中转区，同壁纸只留最新一份即可；
    不同名壁纸（其他角色/装束）全部保留。keep 为本次项目路径（绝对或相对均可）。

    目录名格式：{character}_{name}_{uuid8}，新版为 无期迷途_{character}_{name}_{uuid8}；
    身份由 core.wallpaper.project_identity 归一（去掉 _uuid8 与标题前缀）。
    """
    wp = ROOT / "resources" / "wallpapers"
    if not wp.is_dir():
        return 0
    keep_path = Path(keep).resolve()
    keep_name = keep_path.name
    # 身份按去 _uuid8 + 去标题前缀归一，升级前导出的旧目录也能一并清理
    prefix = project_identity(keep_name)
    removed = 0
    for child in wp.iterdir():
        if not child.is_dir():
            continue
        if child.resolve() == keep_path:
            continue
        # 同角色同装束：目录名归一后与本次身份一致；不同名则保留
        if project_identity(child.name) != prefix:
            continue
        shutil.rmtree(child, ignore_errors=True)
        removed += 1
    if removed:
        print(f"[export] 已清理 {removed} 个同装束旧导出项目目录", flush=True)
    return removed


def apply_skin(character: str, bundle: str, name: str | None, options: dict) -> dict:
    """导出并直接应用到 Wallpaper Engine（新副本 + openWallpaper，绕开编辑器导入路径）。"""
    try:
        proj = _export_project(character, bundle, name, options)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    ok = apply_wallpaper(Path(proj))
    if not ok:
        return {
            "ok": False,
            "project": proj,
            "error": "应用失败：未找到 Wallpaper Engine 安装目录",
        }
    # 应用成功才清理旧导出（失败时保留旧导出，用户可能手动拖入本次项目）
    cleaned = _cleanup_stale_exports(proj)
    return {
        "ok": True,
        "project": proj,
        "cleaned": cleaned,
        "message": "已生成并应用壁纸，可在 Wallpaper Engine 中查看",
    }


def _is_cancelled(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _download_cancelled(skin: dict, downloaded: list[dict]) -> dict:
    return {
        "ok": False,
        "cancelled": True,
        "skin": skin,
        "downloaded": downloaded,
        "error": "下载已取消",
    }


def _extract_static_from_chunk(
    chunk_path: Path,
    target,
    cancel_event: threading.Event | None,
) -> dict:
    """流式扫描 chunk，仅导出目标 Texture2D 中面积最大的图。"""
    import UnityPy
    from tools.split_ptn_chunk import iter_blocks

    best_image = None
    best_size = (0, 0)
    seen = 0
    for index, (data, _next_off) in enumerate(iter_blocks(chunk_path, cancel_event), 1):
        if _is_cancelled(cancel_event):
            raise RuntimeError("已取消")
        _emit_stage("正在扫描静态资源块", detail=f"第 {index} 块")
        try:
            env = UnityPy.load(data)
        except Exception:  # noqa: BLE001
            continue
        for obj in env.objects:
            if _is_cancelled(cancel_event):
                raise RuntimeError("已取消")
            if obj.type.name != "Texture2D":
                continue
            try:
                texture = obj.read()
                if (texture.m_Name or "").casefold() != target.asset_name.casefold():
                    continue
                seen += 1
                size = (int(texture.m_Width), int(texture.m_Height))
                if size[0] * size[1] > best_size[0] * best_size[1]:
                    best_image = texture.image.copy()
                    best_size = size
            except Exception:  # noqa: BLE001
                continue
    if best_image is None:
        raise RuntimeError(
            f"chunk {target.chunk} 中未找到 Texture2D {target.asset_name}"
        )
    out = ROOT / "resources" / "extracted" / "static" / target.painting
    out.mkdir(parents=True, exist_ok=True)
    destination = out / "painting.png"
    best_image.save(destination)
    return {"path": str(destination), "size": list(best_size), "matches": seen}


def _extract_static_from_bundle_bytes(
    raw: bytes,
    target,
    cancel_event: threading.Event | None,
) -> dict:
    """从补丁 ZIP 的单 bundle 字节中提取目标最大 Texture2D。"""
    import UnityPy
    from tools.split_ptn_chunk import read_block

    block = read_block(raw, 0)
    data = block[0] if block else raw
    env = UnityPy.load(data)
    best_image = None
    best_size = (0, 0)
    seen = 0
    for obj in env.objects:
        if _is_cancelled(cancel_event):
            raise RuntimeError("已取消")
        if obj.type.name != "Texture2D":
            continue
        try:
            texture = obj.read()
            if (texture.m_Name or "").casefold() != target.asset_name.casefold():
                continue
            seen += 1
            size = (int(texture.m_Width), int(texture.m_Height))
            if size[0] * size[1] > best_size[0] * best_size[1]:
                best_image = texture.image.copy()
                best_size = size
        except Exception:  # noqa: BLE001
            continue
    if best_image is None:
        raise RuntimeError(f"补丁资源中未找到 Texture2D {target.asset_name}")
    out = ROOT / "resources" / "extracted" / "static" / target.painting
    out.mkdir(parents=True, exist_ok=True)
    destination = out / "painting.png"
    best_image.save(destination)
    return {"path": str(destination), "size": list(best_size), "matches": seen}


def _live2d_family_extras(model: str) -> list[dict]:
    """读取 Live2D 家族跨 chunk 索引：贴图/材质/特效可能落在 moc 之外的 chunk。"""
    path = ROOT / "resources" / "metadata" / "live2d_family_bundles.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, dict):
        return []
    entry = models.get(model)
    if entry is None:
        folded = str(model).casefold()
        entry = next((v for k, v in models.items() if str(k).casefold() == folded), None)
    bundles = entry.get("bundles") if isinstance(entry, dict) else None
    return [item for item in bundles or [] if isinstance(item, dict)]


def _fetch_live2d_family_blocks(source, target, cancel_event, downloaded: list[dict]):
    """按 Range 补取 moc chunk 之外的家族 bundle，返回装配管线可用的块。"""
    from tools.split_ptn_chunk import read_block

    blocks: list[tuple[bytes, int]] = []
    for item in _live2d_family_extras(target.model_name):
        chunk = str(item.get("chunk") or "")
        offset = int(item.get("offset") or 0)
        length = int(item.get("length") or 0)
        if not chunk or chunk == target.chunk or length <= 0:
            continue  # moc chunk 已整包下载，无需重复取
        if _is_cancelled(cancel_event):
            raise RuntimeError("已取消")
        name = str(item.get("name") or chunk)
        label = name.rsplit("/", 1)[-1]
        _emit_stage("正在下载 Live2D 家族资源", detail=f"{label} {length / 1048576:.1f} MiB")
        raw = source.download_chunk_range(
            chunk,
            offset,
            length,
            cancel_event=cancel_event,
            emit=lambda stage, detail="": _emit_stage(stage, detail=detail),
            label=label,
        )
        block = read_block(raw, 0)
        if block is None:
            raise RuntimeError(f"Live2D 家族资源解密失败: {name}")
        blocks.append((block[0], 0))
        downloaded.append(
            {"chunk": chunk, "offset": offset, "length": length, "name": name, "ok": True}
        )
    return blocks


def _normalize_downloaded_motions(out_dir: Path, model: str) -> None:
    """chunk 通道与 classify 通道统一动作命名（clip_* → Idle/Background/Click_*）。"""
    internal = str(model).rsplit("_", 1)[0].casefold()
    if not internal:
        return
    try:
        from tools.assemble_classify import normalize_motions

        normalize_motions(
            out_dir,
            internal,
            emit=lambda stage, detail="": _emit_stage(stage, detail=detail),
        )
    except Exception as exc:  # noqa: BLE001
        _emit_stage("动作名规范化跳过", detail=str(exc)[:160])


def _rebuild_and_link_library() -> None:
    """按要求完成本地索引、图鉴挂接与内存库刷新。"""
    rc, _out, err = run_tool("build_local_index", timeout=120)
    if rc:
        raise RuntimeError(f"build_local_index 失败: {err.decode('utf-8', 'ignore')[-500:]}")
    rc, _out, err = run_tool("link_skins", timeout=120)
    if rc:
        raise RuntimeError(f"link_skins 失败: {err.decode('utf-8', 'ignore')[-500:]}")
    metadata.reload()
    refresh_library()


def _download_skin_classify(
    skin: dict,
    source,
    target,
    cancel_event: threading.Event | None,
    downloaded: list[dict],
) -> dict:
    """Live2D 走官方 classify slice 通道：按组 Range 下载 → PTN 工具链 → 项目目录。"""
    painting = target.painting
    live2d_root = ROOT / "resources" / "extracted" / "live2d"
    output = live2d_root / painting
    ready = output / "model3.json"
    if not ready.exists():
        # moc 名大小写可能与 painting 不同，按 casefold 找既有目录
        for cand in live2d_root.glob("*"):
            if cand.is_dir() and cand.name.lower() == painting and (cand / "model3.json").exists():
                output = cand
                ready = cand / "model3.json"
                break
    if ready.exists():
        _emit_stage("资源已下载，跳过 CDN", detail=painting)
        with _INDEX_LOCK:
            _emit_stage("正在同步本地索引", detail=painting)
            _rebuild_and_link_library()
        return {
            "ok": True,
            "skipped": True,
            "skin": skin,
            "downloaded": [],
            "asset": str(ready),
        }

    staging = ROOT / "resources" / "bundles" / "ptn_classify" / target.group_id
    ok = False
    try:
        paths = source.download_classify_group(
            target.group_id, staging,
            cancel_event=cancel_event,
            emit=lambda stage, detail="": _emit_stage(stage, detail=detail),
        )
        downloaded.append({"group": target.group_id, "bundles": [str(p) for p in paths], "ok": True})
        if _is_cancelled(cancel_event):
            return _download_cancelled(skin, downloaded)

        from tools.assemble_classify import assemble_from_group

        result = assemble_from_group(
            staging, live2d_root, painting,
            cancel_event=cancel_event,
            emit=lambda stage, detail="": _emit_stage(stage, detail=detail),
        )
        asset = Path(result["dir"]) / result["model"]
        ok = True
        with _INDEX_LOCK:
            _emit_stage("正在同步本地索引", detail=painting)
            _rebuild_and_link_library()
        _emit_stage("装束下载完成", detail=painting)
        return {
            "ok": True,
            "skin": skin,
            "target": {
                "type": "live2d_classify",
                "painting": painting,
                "group": target.group_id,
                "internal": target.internal,
                "seq": target.seq,
            },
            "downloaded": downloaded,
            "asset": str(asset),
        }
    except Exception as e:  # noqa: BLE001
        if _is_cancelled(cancel_event) or str(e) == "已取消":
            return _download_cancelled(skin, downloaded)
        error = str(e).strip() or type(e).__name__
        if "codec" in error or "not callable" in error:
            import traceback as _tb
            (ROOT / "resources" / "bundles" / "ptn_classify_last_error.txt").write_text(
                _tb.format_exc(), encoding="utf-8")
        _emit_stage("装束下载失败", detail=error[:300])
        return {"ok": False, "skin": skin, "downloaded": downloaded, "error": error}
    finally:
        if ok:
            shutil.rmtree(staging, ignore_errors=True)


def download_skin(skin: dict, cancel_event: threading.Event | None = None) -> dict:
    """按 library_state 精确定位并下载、提取一个 PTN 装束；Live2D 优先走 classify 通道。"""
    source = registry.get("sources", "ptn_cdn")
    if source is None:
        return {"ok": False, "status": 501, "error": "PTN CDN 来源插件未注册"}

    downloaded: list[dict] = []
    chunk_path: Path | None = None
    try:
        if str(skin.get("type")) == "live2d":
            from plugins.sources.ptn_cdn import resolve_classify_target

            classify = resolve_classify_target(skin)
            if classify is not None:
                return _download_skin_classify(skin, source, classify, cancel_event, downloaded)

        from plugins.sources.ptn_cdn import load_library_state, resolve_skin_target

        state_path = ROOT / "resources" / "metadata" / "library_state.json"
        target = resolve_skin_target(skin, load_library_state(state_path))
        output_kind = "static" if target.kind == "static_patch" else target.kind
        output_name = target.model_name if output_kind == "live2d" else target.painting
        output = ROOT / "resources" / "extracted" / output_kind / output_name
        ready = (
            output / "painting.png" if output_kind == "static" else output / "model3.json"
        )
        if ready.exists():
            _emit_stage("资源已下载，跳过 CDN", detail=target.painting)
            with _INDEX_LOCK:
                _emit_stage("正在同步本地索引", detail=target.painting)
                _rebuild_and_link_library()
            return {
                "ok": True,
                "skipped": True,
                "skin": skin,
                "downloaded": [],
                "asset": str(ready),
            }

        if _is_cancelled(cancel_event):
            return _download_cancelled(skin, downloaded)
        if target.kind == "static_patch":
            patch_index = _read_json(
                ROOT / "resources" / "metadata" / "patch_resource_index.json", {}
            ).get("resources", {})
            record = patch_index.get(target.asset_name)
            if not isinstance(record, dict):
                raise RuntimeError(f"补丁资源索引缺少 {target.asset_name}")
            _emit_stage("正在下载补丁内目标资源", detail=target.asset_name)
            raw_bundle = source.download_patch_entry(
                record,
                cancel_event=cancel_event,
                emit=lambda stage, detail="": _emit_stage(stage, detail=detail),
            )
            downloaded.append({"patch": record.get("patch"), "entry": record.get("entry"), "ok": True})
            asset = _extract_static_from_bundle_bytes(raw_bundle, target, cancel_event)
        else:
            work = ROOT / "resources" / "bundles" / "ptn_chunks"
            _emit_stage("正在下载 CDN chunk", detail=target.chunk)
            chunk_path = source.download_chunk(
                target.chunk,
                work,
                cancel_event=cancel_event,
                emit=lambda stage, detail="": _emit_stage(stage, detail=detail),
            )
            downloaded.append({"path": str(chunk_path), "chunk": target.chunk, "ok": True})

            if _is_cancelled(cancel_event):
                return _download_cancelled(skin, downloaded)
            if target.kind == "static":
                _emit_stage("正在提取目标静态立绘", detail=target.asset_name)
                asset = _extract_static_from_chunk(chunk_path, target, cancel_event)
            else:
                from tools.assemble_live2d import assemble_from_chunk

                # PTN 的 Live2D 家族可能跨 chunk：moc/动画在下到的 chunk，
                # 贴图/材质/特效在另一个 chunk，按索引 Range 补取后一起装配。
                extra_blocks = _fetch_live2d_family_blocks(
                    source, target, cancel_event, downloaded
                )
                _emit_stage("正在装配 Live2D 模型", detail=target.model_name)
                asset = assemble_from_chunk(
                    target.model_name,
                    chunk_path,
                    output,
                    cancel_event=cancel_event,
                    emit=lambda stage, detail="": _emit_stage(stage, detail=detail),
                    extra_blocks=extra_blocks,
                )
                _normalize_downloaded_motions(output, target.model_name)

        if _is_cancelled(cancel_event):
            return _download_cancelled(skin, downloaded)
        with _INDEX_LOCK:
            _emit_stage("正在同步本地索引", detail=target.painting)
            _rebuild_and_link_library()
        _emit_stage("装束下载完成", detail=target.painting)
        return {
            "ok": True,
            "skin": skin,
            "target": {
                "type": target.kind,
                "painting": target.painting,
                "model": target.model_name,
                "chunk": target.chunk,
            },
            "downloaded": downloaded,
            "asset": asset,
        }
    except Exception as e:  # noqa: BLE001
        if _is_cancelled(cancel_event) or str(e) == "已取消":
            return _download_cancelled(skin, downloaded)
        error = str(e).strip() or type(e).__name__
        import traceback as _tb
        (ROOT / "resources" / "bundles" / "ptn_classify_last_error.txt").write_text(
            _tb.format_exc(), encoding="utf-8")
        _emit_stage("装束下载失败", detail=error[:300])
        return {"ok": False, "skin": skin, "downloaded": downloaded, "error": error}
    finally:
        # 完成、失败或取消均不保留超大的 chunk；Range .part 留下供取消后续传。
        if chunk_path is not None:
            try:
                chunk_path.unlink(missing_ok=True)
            except OSError as e:
                print(f"[download] 删除 chunk 失败 {chunk_path}: {e}", flush=True)


def open_wallpapers_dir() -> dict:
    """打开软件自己的壁纸输出目录（resources/wallpapers，存放导出的待用壁纸项目）。"""
    d = ROOT / "resources" / "wallpapers"
    d.mkdir(parents=True, exist_ok=True)
    os.startfile(str(d))  # noqa: S606
    return {"ok": True, "path": str(d)}


def clean_exports() -> dict:
    """清理导出的壁纸项目与截图（resources/wallpapers + resources/exports）。"""
    removed = []
    for sub in ("wallpapers", "exports"):
        d = ROOT / "resources" / sub
        if d.is_dir():
            for child in d.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
                removed.append(str(child))
    return {"ok": True, "removed": len(removed)}


class Handler(BaseHTTPRequestHandler):
    def _query(self, key: str, default: str = "") -> str:
        """解析查询串 ?key=value。

        unquote_plus 而非 unquote：前端用 URLSearchParams 编码，空格会变成 "+"，
        只做 unquote 会留下字面加号，让「诺克斯 装束 501」这类含空格的装束名永远匹配不上。
        """
        import urllib.parse

        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        for part in qs.split("&"):
            k, _, v = part.partition("=")
            if k == key:
                return urllib.parse.unquote_plus(v)
        return default

    def _send(self, status: int, payload: dict) -> None:
        # 诊断日志：记录前端每次 API 调用的请求与响应（真实窗口排障用）
        try:
            print(
                f"[api] {self.command} {self.path} -> {status} "
                f"{json.dumps(payload, ensure_ascii=False)[:300]}",
                flush=True,
            )
        except Exception:  # noqa: BLE001
            pass
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream_events(self) -> None:
        """SSE 长连接：把下载/提取/同步等阶段事件推送给前端。"""
        q: queue.Queue = queue.Queue(maxsize=200)
        with _EVENT_LOCK:
            _EVENT_QUEUES.append(q)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"event: connected\ndata: {}\n\n")
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=30)
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")  # 心跳保活
                    self.wfile.flush()
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _EVENT_LOCK:
                if q in _EVENT_QUEUES:
                    _EVENT_QUEUES.remove(q)

    def _is_local_request(self) -> bool:
        """只允许本机页面调用后端：Host 必须为本机地址，且 Origin（浏览器跨站请求必带）
        若存在也必须是本机。这样即使网页被浏览器打开的其他恶意网站请求，也会被拒绝，
        避免恶意网页借本机 API 删除下载/触发下载占满磁盘。"""
        host = (self.headers.get("Host") or "").split(":", 1)[0].strip().lower()
        if host not in ("127.0.0.1", "localhost", "::1"):
            return False
        origin = self.headers.get("Origin") or ""
        if not origin:
            return True  # 同源请求 / 非浏览器客户端（curl、pywebview 等）
        from urllib.parse import urlparse

        return (urlparse(origin).hostname or "").lower() in ("127.0.0.1", "localhost", "::1")

    def _guard_local(self) -> bool:
        """非本机请求时直接返回 403，返回 False 表示调用方应停止处理。"""
        if self._is_local_request():
            return True
        self._send(403, {"ok": False, "error": "forbidden: 仅允许本机调用"})
        return False

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._guard_local():
            return
        self._send(200, {"ok": True})

    def do_GET(self) -> None:  # noqa: N802
        if not self._guard_local():
            return
        try:
            ensure_fresh_metadata()
            if self.path.startswith("/api/events"):
                self._stream_events()
                return
            if self.path.startswith("/api/health"):            self._send(200, {"ok": True, "plugins": registry.summary()})
            elif self.path.startswith("/api/library/downloaded"):
                self._send(200, {"ok": True, "skins": downloaded_with_painting()})
            elif self.path.startswith("/api/config"):
                self._send(200, get_config())
            elif self.path.startswith("/api/upscale/versions"):
                character = self._query("character", "")
                bundle = self._query("bundle", "")
                name = self._query("name", "") or None
                skin = resolve_skin(character, bundle, name)
                asset = local_skin_asset(character, bundle, name)
                if not skin or not asset:
                    self._send(200, {"ok": False, "error": "装束未下载或不存在", "versions": [], "base_resolution": "", "algorithms": UPSCALE_ALGORITHMS})
                else:
                    stype = skin.get("type", "live2d")
                    res = get_skin_upscale_versions(stype, asset.get("dir", ""))
                    self._send(200, res)
            elif self.path.startswith("/api/voice/lines"):
                character = self._query("character", "")
                bundle = self._query("bundle", "")
                name = self._query("name", "") or None
                self._send(200, get_voice_lines(character, bundle, name))
            else:
                self._send(404, {"ok": False, "error": "not found"})
        except Exception:  # noqa: BLE001
            logger.exception("GET %s failed", self.path)
            try:
                self._send(500, {"ok": False, "error": "internal error"})
            except Exception:  # noqa: BLE001
                pass

    def do_POST(self) -> None:  # noqa: N802
        if not self._guard_local():
            return
        try:
            ensure_fresh_metadata()
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
            if self.path.startswith("/api/"):
                print(
                    f"[api] POST {self.path} body={json.dumps(data, ensure_ascii=False)[:200]}",
                    flush=True,
                )
            if self.path.startswith("/api/download/cancel"):
                cancel_download(data.get("download_id") or "")
                self._send(200, {"ok": True})
            elif self.path.startswith("/api/download"):
                character = data.get("character")
                bundle = data.get("bundle")
                name = data.get("name")
                download_id = data.get("download_id") or ""
                skin = resolve_skin(character, bundle, name)
                if not skin:
                    self._send(404, {"ok": False, "error": f"装束不存在: {character}/{bundle}"})
                    return
                cancel_event = _register_cancel(download_id) if download_id else None
                try:
                    result = download_skin(skin, cancel_event)
                finally:
                    if download_id:
                        _unregister_cancel(download_id)
                # 未实现来源插件时返回 501，前端可据此提示“功能开发中”
                self._send(501 if result.get("status") == 501 else 200, result)
            elif self.path.startswith("/api/library/delete"):
                self._send(
                    200,
                    delete_skin_files(
                        data.get("character"), data.get("bundle"), data.get("name")
                    ),
                )
            elif self.path.startswith("/api/library/delete-batch"):
                # 批量删除：items=[{character,bundle,name},...]，只重建一次索引（快）
                items = data.get("items") or []
                if not isinstance(items, list) or not items:
                    self._send(400, {"ok": False, "error": "items 为空"})
                else:
                    self._send(200, delete_skins_batch(items))
            elif self.path.startswith("/api/library/cleanup-bundles"):
                self._send(200, cleanup_processed_bundles())
            elif self.path.startswith("/api/library/clear"):
                self._send(200, clear_all_downloads())
            elif self.path.startswith("/api/metadata/update"):
                self._send(200, run_metadata_update())
            elif self.path.startswith("/api/metadata/sync-wiki"):
                self._send(200, run_wiki_sync())
            elif self.path.startswith("/api/voice/sync-lines"):
                self._send(200, run_voice_sync(data.get("characters") or []))
            elif self.path.startswith("/api/config"):
                self._send(200, set_config(data.get("config") or {}))
            elif self.path.startswith("/api/log"):
                # 前端运行时错误上报（排障用）：打印到 stdout，不落盘
                print(f"[fe-error] {json.dumps(data, ensure_ascii=False)[:800]}", flush=True)
                self._send(200, {"ok": True})
            elif self.path.startswith("/api/export-image-data"):
                self._send(
                    200,
                    export_image_data(
                        data.get("character"),
                        data.get("bundle"),
                        data.get("name"),
                        data.get("dataUrl") or "",
                        index=data.get("index"),
                    ),
                )
            elif self.path.startswith("/api/export-image"):
                self._send(
                    200,
                    export_static_image(
                        data.get("character"),
                        data.get("bundle"),
                        data.get("name"),
                        data.get("upscaleVersion"),
                    ),
                )
            elif self.path.startswith("/api/upscale/delete"):
                character = data.get("character")
                bundle = data.get("bundle")
                name = data.get("name")
                version = data.get("version") or ""
                skin = resolve_skin(character, bundle, name)
                asset = local_skin_asset(character, bundle, name)
                if not skin or not asset:
                    self._send(404, {"ok": False, "error": "装束不存在或未下载"})
                else:
                    stype = skin.get("type", "live2d")
                    res = delete_skin_upscale_version(stype, asset.get("dir", ""), version)
                    self._send(200, res)
            elif self.path.startswith("/api/upscale"):
                character = data.get("character")
                bundle = data.get("bundle")
                name = data.get("name")
                scale = int(data.get("scale") or 2)
                algorithm = data.get("algorithm") or "waifu2x"
                denoise = int(data.get("denoise") or 0)
                tta = bool(data.get("tta", False))
                downsample = bool(data.get("downsample", False))

                skin = resolve_skin(character, bundle, name)
                asset = local_skin_asset(character, bundle, name)
                if not skin or not asset:
                    self._send(404, {"ok": False, "error": f"装束不存在或未下载: {character}/{bundle}"})
                else:
                    stype = skin.get("type", "live2d")
                    _emit_stage("正在超分", detail=f"{character} · {skin.get('name', '')} ({algorithm} {scale}x)")
                    try:
                        res = upscale_skin_asset(
                            stype,
                            asset.get("dir", ""),
                            scale=scale,
                            algorithm=algorithm,
                            denoise=denoise,
                            tta=tta,
                            downsample=downsample,
                        )
                        _emit_stage("done", ok=res.get("ok", False), detail=res.get("message", ""))
                        self._send(200, res)
                    except Exception as ex:  # noqa: BLE001
                        logger.exception("超分失败: %s", character)
                        _emit_stage("done", ok=False, detail=str(ex))
                        self._send(200, {"ok": False, "error": str(ex)})
            elif self.path.startswith("/api/export"):
                self._send(
                    200,
                    export_skin(
                        data.get("character"),
                        data.get("bundle"),
                        data.get("name"),
                        data.get("options") or {},
                    ),
                )
            elif self.path.startswith("/api/apply"):
                self._send(
                    200,
                    apply_skin(
                        data.get("character"),
                        data.get("bundle"),
                        data.get("name"),
                        data.get("options") or {},
                    ),
                )
            elif self.path.startswith("/api/palette"):
                character = data.get("character")
                bundle = data.get("bundle")
                name = data.get("name")
                mode = data.get("mode") or "auto"
                bg_color = data.get("bgColor")
                skin = resolve_skin(character, bundle, name)
                asset = local_skin_asset(character, bundle, name)
                # 纯色背景只依赖用户选色，不要求素材已下载/可解码：
                # 取色失败时 mode_css 仍会返回显式颜色，其他模式回退固定样式，
                # 避免前端拿到空 CSS 导致「选了颜色但背景不变」。
                src = find_palette_source({**skin, "asset": asset}, ROOT) if skin and asset else None
                palette = extract_palette(src) if src else None
                css = mode_css(mode, palette, solid_color=bg_color) or bg_css(mode)
                self._send(
                    200,
                    {
                        "ok": bool(palette) or (mode == "solid" and bool(bg_color)),
                        "colors": palette or fallback_palette(),
                        "source": str(src) if src else None,
                        "css": css,
                    },
                )
            elif self.path.startswith("/api/open/download-dir"):
                os.startfile(str(ROOT / "resources" / "bundles"))  # noqa: S606
                self._send(200, {"ok": True})
            elif self.path.startswith("/api/open/extracted-dir"):
                os.startfile(str(ROOT / "resources" / "extracted"))  # noqa: S606
                self._send(200, {"ok": True})
            elif self.path.startswith("/api/open/wallpapers-dir"):
                self._send(200, open_wallpapers_dir())
            elif self.path.startswith("/api/library/clean-exports"):
                self._send(200, clean_exports())
            else:
                self._send(404, {"ok": False, "error": "not found"})
        except Exception as e:  # noqa: BLE001
            logger.exception("POST %s failed", self.path)
            self._send(500, {"ok": False, "error": str(e)})

    def log_message(self, *args) -> None:  # noqa: D401
        pass


def main(port: int = 8766) -> None:
    """启动后端 API 服务。

    注意：打包版/桌面版会以线程方式调用 main(8770) 传入端口，
    因此这里**不再**从 sys.argv 读端口（避免被启动参数意外覆盖，
    导致前端连不上后端）。
    """
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"backend API listening on http://127.0.0.1:{port}")
    logger.info("backend API listening on http://127.0.0.1:%s", port)
    server.serve_forever()


if __name__ == "__main__":
    _port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    main(_port)
