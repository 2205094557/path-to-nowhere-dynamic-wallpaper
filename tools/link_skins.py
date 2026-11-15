# -*- coding: utf-8 -*-
"""把本地提取的立绘资源挂接到图鉴 skins.json。

BWiki skins.json 只有官方装束目录；本地提取的资源（local_skins.json）需要：
1. 与 BWiki 装束匹配的（拼音命中）→ 回填该装束的 painting 键与 type=static；
2. 无法对应 BWiki 装束的（基础立绘/差分/未收录）→ 追加合成条目，保证图鉴可见。
合成条目带 "source": "local_extract" 标记，不污染官方数据语义。

用法：
    python tools/link_skins.py
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "resources" / "metadata"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def norm_en(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", value or "").lower())


def pinyin_key(cn: str) -> str:
    try:
        from pypinyin import lazy_pinyin
        return "".join(lazy_pinyin(cn)).lower()
    except ImportError:  # noqa: BLE001
        return ""


def main() -> int:
    skins = json.loads((MD / "skins.json").read_text(encoding="utf-8"))["skins"]
    local = json.loads((MD / "local_skins.json").read_text(encoding="utf-8"))
    # 幂等同步：先移除上次由本地提取追加、但当前 local_skins 已不存在的条目。
    local_paintings = {item.get("painting") for item in local if item.get("painting")}
    skins = [
        skin for skin in skins
        if skin.get("source") != "local_extract" or skin.get("painting") in local_paintings
    ]

    # BWiki 装束按 (character, 拼音key) 建索引
    by_key: dict[tuple[str, str], dict] = {}
    for sk in skins:
        by_key.setdefault((sk.get("character", ""), pinyin_key(sk.get("name", ""))), sk)

    linked, appended = 0, 0
    existing_paintings = {sk.get("painting") for sk in skins}
    for loc in local:
        if loc.get("type") not in ("static", "live2d") or not loc.get("character"):
            continue
        m = re.match(r"^lihui_[A-Za-z0-9]+?_(.+)$", loc["name"], re.I)
        skin_key = norm_en(m.group(1)) if m else norm_en(loc["name"])
        # 差分序号剥离：xxx2/xxx3 视为同一装束的差分
        base_key = re.sub(r"\d+$", "", skin_key) or skin_key
        target = by_key.get((loc["character"], skin_key)) or by_key.get((loc["character"], base_key))
        if target and not target.get("painting"):
            target["painting"] = loc["painting"]
            target.setdefault("type", loc.get("type", "static"))
            linked += 1
        elif loc["painting"] not in existing_paintings:
            skins.append({
                "character": loc["character"],
                "name": (m.group(1) if m else loc["name"]),
                "bundle": "",
                "painting": loc["painting"],
                "type": loc.get("type", "static"),
                "series": "",
                "source": "local_extract",
            })
            existing_paintings.add(loc["painting"])
            appended += 1

    (MD / "skins.json").write_text(
        json.dumps({"schema_version": 1, "skins": skins}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"挂接完成: 回填官方装束 {linked} 条, 追加本地条目 {appended} 条, 总装束 {len(skins)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
