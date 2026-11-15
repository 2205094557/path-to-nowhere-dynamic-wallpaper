# -*- coding: utf-8 -*-
"""重建精简版无期迷途图鉴（skins.json）。

规范：
  - 去除所有静态升阶！
  - 只有真正拥有 Live2D 动态立绘（_2 模型）的狂级角色才保留“升阶”；
  - 普级/危级等在原作中只有静态升阶原画的角色，彻底不生成“升阶”条目；
  - 官方皮肤按 BWiki 上线时间严格对应 _3, _4, ...
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "resources" / "metadata"
L2D = ROOT / "resources" / "extracted" / "live2d"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def parse_date(x: dict) -> datetime:
    d = x.get("release_date") or ""
    try:
        return datetime.strptime(d, "%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return datetime.max


def get_l2d_shengjie_characters(rev_alias: dict[str, str]) -> set[str]:
    """获取所有真正拥有 Live2D 动态升阶（_2 模型）的角色中文名。"""
    chars = set()
    if not L2D.exists():
        return chars
    for p in L2D.iterdir():
        if p.is_dir() and p.name.endswith("_2"):
            stem = re.sub(r"_2$", "", p.name, flags=re.I).lower()
            stem = re.sub(r"^char2d_", "", stem, flags=re.I)
            cn = rev_alias.get(stem)
            if cn:
                chars.add(cn)
    return chars


def main() -> int:
    chars = json.load((MD / "characters.json").open(encoding="utf-8"))["characters"]
    bwiki_skins = json.load((MD / "skins_bwiki.json").open(encoding="utf-8"))["skins"]
    aliases = json.load((MD / "name_aliases.json").open(encoding="utf-8"))
    cn_to_alias = {cn: str(en).lower() for cn, en in aliases.items()}
    rev_alias = {str(en).lower(): cn for cn, en in aliases.items()}

    l2d_shengjie_chars = get_l2d_shengjie_characters(rev_alias)
    print(f"检测到拥有真实 Live2D 动态升阶的角色共 {len(l2d_shengjie_chars)} 名")

    skins_by_char: dict[str, list[dict]] = {}
    for s in bwiki_skins:
        c = s.get("character")
        if c:
            skins_by_char.setdefault(c, []).append(s)

    for c in skins_by_char:
        skins_by_char[c].sort(key=parse_date)

    clean_skins: list[dict] = []
    mapping_summary: dict[str, dict[str, str]] = {}

    for c in chars:
        cn = c["name"]
        alias = cn_to_alias.get(cn, cn.lower())
        mapping_summary[cn] = {}

        # 1. 只有真正拥有 Live2D 动态升阶（_2）的角色才保留“升阶”动态立绘
        #    对于只有静态升阶原画的非狂级角色，彻底去除“升阶”条目
        if cn in l2d_shengjie_chars:
            clean_skins.append({
                "character": cn,
                "name": "升阶",
                "type": "live2d",
                "bundle": "",
                "painting": f"{alias}_2",
                "series": "升阶三阶",
            })
            mapping_summary[cn]["2"] = "升阶"

        # 2. 官方各期皮肤（按上线顺序依次对应 _3, _4, _5...）
        c_skins = skins_by_char.get(cn, [])
        for idx, s in enumerate(c_skins, start=3):
            skin_name = s.get("name") or f"装束 {idx}"
            clean_skins.append({
                "character": cn,
                "name": skin_name,
                "type": "live2d",
                "bundle": "",
                "painting": f"{alias}_{idx}",
                "series": s.get("series", ""),
                "release_date": s.get("release_date", ""),
            })
            mapping_summary[cn][str(idx)] = skin_name

    # 写入精简版 skins.json
    out_path = MD / "skins.json"
    out_path.write_text(
        json.dumps({"schema_version": 1, "skins": clean_skins}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"图鉴重构完成: 共收录 {len(clean_skins)} 款装束（已彻底去除所有静态升阶） -> {out_path}")

    # 同时写入一份映射表
    map_path = MD / "skin_sequence_map.json"
    map_path.write_text(
        json.dumps(mapping_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
