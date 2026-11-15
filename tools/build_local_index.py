# -*- coding: utf-8 -*-
"""扫描 resources/extracted → 生成 local_skins.json（自动标记本地已提取装束）。

M0 通用骨架：
  - live2d：目录内递归识别 *.model3.json，模型目录整体作为一个装束资源；
  - static：目录内识别 painting 级产物 painting.png（Texture2D 导出/缝合结果）。

条目仅携带目录名派生的占位字段（character/bundle 留空，name 取目录名），
与 characters.json/skins.json 的精确匹配、完整命名规则由 M4 里程碑补齐。
"""
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "resources" / "metadata"
EXTRACTED = ROOT / "resources" / "extracted"


def _load_catalog_skins() -> list[dict]:
    path = MD / "skins.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("skins", []) if isinstance(data, dict) else []
    except Exception:  # noqa: BLE001
        return []


def _catalog_match(dir_name: str, stype: str, skins: list[dict] | None = None) -> dict | None:
    skins_list = skins if skins is not None else _load_catalog_skins()
    # 1. 尝试直接按 painting 匹配
    norm_name = dir_name.lower()
    for s in skins_list:
        if s.get("type") == stype and str(s.get("painting") or "").lower() == norm_name:
            return s

    # 2. 剥离 char2d_ 前缀并解析 {alias}_{number}
    clean = re.sub(r"^char2d_", "", dir_name, flags=re.I)
    m = re.match(r"^([a-zA-Z0-9]+)_(\d+)$", clean)
    if m:
        alias_prefix = m.group(1).lower()
        stage_num = m.group(2)
        norm_key = f"{alias_prefix}_{stage_num}"
        for s in skins_list:
            if s.get("type") == stype and str(s.get("painting") or "").lower() == norm_key:
                return s

    return None


def scan_live2d() -> list[dict]:
    """扫描 extracted/live2d：含 model3.json 的目录视为一个 Live2D 装束资源。"""
    out = []
    catalog_skins = _load_catalog_skins()
    l2d_dir = EXTRACTED / "live2d"
    if not l2d_dir.is_dir():
        return out
    for d in sorted(p for p in l2d_dir.iterdir() if p.is_dir()):
        m3 = d / "model3.json"
        if not m3.exists():
            m3 = next(d.rglob("*.model3.json"), None)
        if not m3:
            continue
        catalog = _catalog_match(d.name, "live2d", catalog_skins)
        char_name = (catalog or {}).get("character") or _match_character(d.name)
        display_name = (catalog or {}).get("name")
        target_painting = (catalog or {}).get("painting")

        if not display_name:
            stem = re.sub(r"^char2d_", "", d.name, flags=re.I)
            parts = stem.split("_")
            if len(parts) >= 2 and parts[-1].isdigit():
                stage = parts[-1]
                stage_label = "升阶" if stage == "2" else f"装束 {stage}"
                display_name = f"{char_name} {stage_label}" if char_name else d.name
            else:
                display_name = d.name

        out.append({
            "character": char_name,
            "bundle": (catalog or {}).get("bundle") or "",
            "name": display_name,
            "type": "live2d",
            "painting": (target_painting or d.name).lower(),
            "asset": {
                "dir": "extracted/" + m3.parent.relative_to(EXTRACTED).as_posix(),
                "model": m3.name,
            },
        })
    return out


def scan_static() -> list[dict]:
    """扫描 extracted/static：含 painting.png（painting 级 Texture2D 产物）的目录视为一个静态装束资源。"""
    out = []
    catalog_skins = _load_catalog_skins()
    static_dir = EXTRACTED / "static"
    if not static_dir.is_dir():
        return out
    for d in sorted(p for p in static_dir.iterdir() if p.is_dir()):
        png = d / "painting.png"
        if not png.exists():
            continue
        catalog = _catalog_match(d.name, "static", catalog_skins)
        out.append({
            "character": (catalog or {}).get("character") or _match_character(d.name),
            "bundle": (catalog or {}).get("bundle") or "",
            "name": (catalog or {}).get("name") or d.name,
            "type": "static",
            "painting": d.name.lower(),
            "asset": {"dir": f"extracted/static/{d.name}", "image": "painting.png"},
        })
    return out


def _load_aliases() -> dict[str, str]:
    path = MD / "name_aliases.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _match_character(dir_name: str) -> str:
    """lihui_<内部名/拼音>_<皮肤> 或 <内部名>_<皮肤ID> -> BWiki 角色中文名。"""
    raw_name = re.sub(r"^char2d_", "", dir_name, flags=re.I)
    m = re.match(r"^lihui_([A-Za-z0-9]+?)_", raw_name)
    if not m:
        # Live2D 家族名：Zoya_501 -> 前缀 zoya
        m2 = re.match(r"^([A-Za-z][A-Za-z0-9]+?)_\d", raw_name)
        if not m2:
            return ""
        prefix = m2.group(1).lower()
    else:
        prefix = m.group(1).lower()
    aliases = _load_aliases()
    # 反查表：内部名/拼音 -> 中文名
    rev = {}
    for cn, internal in aliases.items():
        rev.setdefault(str(internal).lower(), cn)
    try:
        from pypinyin import lazy_pinyin
        for cn in aliases:
            rev.setdefault("".join(lazy_pinyin(cn)).lower(), cn)
    except ImportError:  # noqa: BLE001
        pass
    if prefix in rev:
        return rev[prefix]
    for k, cn in rev.items():
        if k.startswith(prefix):
            return cn
    return ""


def main() -> int:
    local = scan_live2d() + scan_static()
    local.sort(key=lambda x: (x["character"], x["bundle"], x["name"]))
    MD.mkdir(parents=True, exist_ok=True)
    (MD / "local_skins.json").write_text(json.dumps(local, ensure_ascii=False, indent=2), encoding="utf-8")
    print("local_skins:", len(local))
    for l in local:
        print(" ", l["character"] or "-", "|", l["bundle"] or "-", "|", l["name"], "|", l["type"], "|", l["asset"]["dir"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
