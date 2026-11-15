# -*- coding: utf-8 -*-
"""classify 组 bundle → 项目 Live2D 目录。

PTN 的 bundle 容器 = [50B UnityFS v7 假头][UnityFS v8 真档]；moc bundle 还在中间
夹了明文 moc3 数据（MOC3 魔数起、至尾部 v8 元数据档止）。标准解析器会被假头骗住，
因此先剥头/提取 moc3，再把剥好的 v8 档喂给 tools/assemble_live2d 的既有管线
（贴图选择 + sssekai AnimationClip→motion3 + 背景合并 + Fade 回退全部复用）。
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import UnityPy

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FAMILY_RE = re.compile(r"^([a-z0-9]+?)_(\d+)$", re.I)
MODEL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*_\d+$")


def _cancel(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("已取消")


def _moc_model_name(env) -> str:
    """moc bundle 的 v8 元数据档里 MonoBehaviour 名即模型名（如 Enfer_3）。"""
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            d = obj.read()
            name = (d.m_Name or "").strip()
            if MODEL_NAME_RE.match(name or ""):
                return name
        except Exception:  # noqa: BLE001
            continue
    return ""


def assemble_from_group(
    game_dir: str | Path,
    out_root: str | Path,
    painting: str,
    *,
    cancel_event=None,
    emit=None,
) -> dict:
    """staging game 目录 → 项目 Live2D 目录（复用 assemble_live2d 全管线）。"""

    def say(stage: str, detail: str = "") -> None:
        if emit:
            emit(stage, detail)
        _cancel(cancel_event)

    m = FAMILY_RE.match(painting.lower())
    if not m:
        raise RuntimeError(f"painting 无法解析出家族名: {painting}")
    internal = m.group(1)
    family = f"{internal}_{m.group(2)}"

    files = sorted(Path(game_dir).rglob("*.bundle"))
    if not files:
        raise RuntimeError(f"staging 目录没有 bundle: {game_dir}")

    blocks: list[tuple[bytes, int]] = []
    moc3: bytes | None = None
    model_name = ""
    for f in files:
        say("正在解析分类资源", f.name)
        _cancel(cancel_event)
        raw = f.read_bytes()
        i_moc = raw.find(b"MOC3")
        i_v8 = raw.rfind(b"UnityFS")
        moc3_here = 0 <= i_moc < i_v8
        body = raw[i_v8:] if i_v8 >= 0 else None
        if body is None or body[:7] != b"UnityFS":
            continue
        env = UnityPy.load(io.BytesIO(body))
        ab_name = ""
        for obj in env.objects:
            if obj.type.name == "AssetBundle":
                try:
                    ab_name = obj.read().m_Name or ""
                except Exception:  # noqa: BLE001
                    pass
                break
        if not ab_name.startswith("assets/art_live2d_"):
            continue  # prefab/timeline 等非家族 bundle，现有管线按前缀过滤
        if moc3_here:
            moc3 = raw[i_moc:i_v8]
            model_name = _moc_model_name(env)
            say("提取 moc3", f"{model_name or family} {len(moc3):,}B")
        blocks.append((body, 0))

    if moc3 is None:
        raise RuntimeError(f"组内未找到 MOC3 数据（家族 {family}）")
    if not model_name:
        model_name = painting
    if model_name.casefold() != painting.lower():
        say("模型名与 painting 不一致", f"{model_name} != {painting}，以 painting 命名")
        model_name = painting

    out_dir = Path(out_root) / model_name
    say("正在装配 Live2D 模型", f"{family} -> {model_name}")
    from tools.assemble_live2d import assemble_live2d

    result = assemble_live2d(
        model_name, blocks, out_dir, cancel_event=cancel_event, emit=emit, moc3_bytes=moc3,
    )
    say("动作名规范化", "对齐本地管线命名，交互/语音映射零改动")
    motions = normalize_motions(out_dir, internal, emit)
    say("装配完成", f"贴图 {result['textures']} 张，动作 {motions} 条")
    return {
        "dir": str(out_dir),
        "model": "model3.json",
        "motions": motions,
        "textures": result["textures"],
        "bundles_parsed": len(blocks),
    }


def canonical_motion_name(stem: str, internal: str) -> str:
    """CDN clip 名（clip_{内部名}_{动作}）→ 本地管线命名（Background/Click_Body…）。

    与本地动作一一对应：剥 clip_ 与家族前缀，下划线分段转大写驼峰。
    IdleCombined（Idle+Background 官方合并待机）规范为 Idle，与本地待机名一致，
    使前端 standbyLabel 的待机判定无需为本渠道单独分支。
    """
    s = stem.casefold()
    if s == "idlecombined":
        return "Idle"
    for prefix in (f"clip_{internal.lower()}_", "clip_"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    m = re.match(rf"^{re.escape(internal.lower())}_(.+)$", s)
    if m:
        s = m.group(1)
    return "_".join(p[:1].upper() + p[1:] for p in s.split("_") if p)


def normalize_motions(out_dir: Path, internal: str, emit=None) -> int:
    """把 assemble_live2d 生成的 clip_* 动作名收敛为本地命名，并重写 model3.json
    为本地形态（Motions: All=全部 + Idle=合并待机），使交互/语音/映射表零改动。"""
    md = Path(out_dir) / "motions"
    if not md.is_dir():
        return 0

    idle_combined = md / "IdleCombined.motion3.json"
    moves: list[tuple[Path, Path]] = []
    for f in sorted(md.glob("*.motion3.json")):
        stem = f.name[: -len(".motion3.json")]
        canon = canonical_motion_name(stem, internal)
        if canon == "Idle" and idle_combined.exists() and f != idle_combined:
            # 未合并的 clip_idle 已被 IdleCombined 取代
            f.unlink()
            continue
        target = md / f"{canon}.motion3.json"
        if target != f:
            moves.append((f, target))

    for src, dst in moves:
        content = src.read_text(encoding="utf-8")
        src.unlink()
        dst.write_text(content, encoding="utf-8")
        if emit:
            emit("动作名规范化", f"{src.stem} -> {dst.stem}")

    all_files = sorted(md.glob("*.motion3.json"),
                       key=lambda f: (f.name != "Idle.motion3.json", f.name))
    motions = {"All": [{"File": f"motions/{f.name}"} for f in all_files]}
    idle = [e for e in motions["All"] if e["File"] == "motions/Idle.motion3.json"]
    if idle:
        motions["Idle"] = idle
    model3_path = Path(out_dir) / "model3.json"
    model3 = json.loads(model3_path.read_text(encoding="utf-8"))
    model3.setdefault("FileReferences", {})["Motions"] = motions
    model3_path.write_text(json.dumps(model3, ensure_ascii=False, indent=1), encoding="utf-8")
    if emit:
        for f in all_files:
            emit("规范动作", f.stem)
    return len(all_files)
