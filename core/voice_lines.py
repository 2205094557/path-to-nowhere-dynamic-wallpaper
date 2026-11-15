"""BWiki 语音台词目录读取与装束缺项继承。

装束专属语音页为节省空间会省略与常服重复的条目。运行时应优先使用装束专属
分类；某一分类为空时，从同角色「升阶」语音池继承，而不是把空数组直接返回。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

CATEGORIES = ("greet", "boring", "idle", "level", "touch", "interact", "interact_exit")
# 二阶段互动池只在二阶段装束上有数据；继承基线（升阶）永远没有这两个池，
# 空池不会被基线填充，正好作为「该装束是否启用二阶段互动规则」的运行时判据。


def load_voice_catalog(root: Path) -> dict[str, dict]:
    path = Path(root) / "resources" / "metadata" / "voice_lines.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    skins = payload.get("skins", {}) if isinstance(payload, dict) else {}
    return skins if isinstance(skins, dict) else {}


def _base_lines(catalog: dict[str, dict], character: str, current_painting: str) -> dict | None:
    candidates = [
        (painting, lines)
        for painting, lines in catalog.items()
        if painting != current_painting
        and isinstance(lines, dict)
        and lines.get("character") == character
    ]
    if not candidates:
        return None
    # PTN 本地命名中 _2 通常是升阶；先按明确中文名，再按 painting 后缀兜底。
    candidates.sort(
        key=lambda item: (
            0 if item[1].get("skin") == "升阶" else 1,
            0 if str(item[0]).casefold().endswith("_2") else 1,
            str(item[0]),
        )
    )
    return candidates[0][1]


def merged_voice_lines(root: Path, painting: str, character: str) -> dict | None:
    """返回当前装束的有效语音池，空分类从同角色升阶语音继承。"""
    catalog = load_voice_catalog(root)
    current = catalog.get(str(painting))
    if not isinstance(current, dict):
        return None
    output = copy.deepcopy(current)
    base = _base_lines(catalog, character, str(painting))
    for category in CATEGORIES:
        pool = output.get(category)
        if not isinstance(pool, list):
            output[category] = []
            pool = output[category]
        if not pool and isinstance(base, dict):
            inherited = base.get(category)
            if isinstance(inherited, list) and inherited:
                output[category] = copy.deepcopy(inherited)
    return output


def category_of_action(lines: dict, action: str) -> str | None:
    """在有效语音池中查找某个 action 所属分类；找不到返回 None。"""
    for category in CATEGORIES:
        pool = lines.get(category)
        if isinstance(pool, list) and any(item.get("action") == action for item in pool):
            return category
    return None

