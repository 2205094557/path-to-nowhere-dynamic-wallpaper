# -*- coding: utf-8 -*-
"""同步无期迷途 BWiki 图鉴到 resources/metadata/。

数据流与 wqmt-bwiki-poc 验证过的探针一致：
    首页 → 角色卡/皮肤图鉴页 → 结构化解析 → imageinfo 补全原图 → characters.json/skins.json

注意 BWiki 存在限流（实测 HTTP 567/429）：请求间隔默认 2 秒。

用法：
    python tools/sync_bwiki_catalog.py            # 联网同步
    python tools/sync_bwiki_catalog.py --offline  # 仅重写已有数据（不联网）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bwiki_lib.client import WikiClient  # noqa: E402
from bwiki_lib.io_utils import atomic_write_json  # noqa: E402
from bwiki_lib.parser import (  # noqa: E402
    attach_imageinfo,
    find_catalog_links,
    parse_character_cards,
    parse_characters,
    parse_skins,
)

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "resources" / "metadata"
RESPONSES = MD / "bwiki_responses"

FALLBACK_PAGES = {"skins": ["皮肤图鉴"]}


def chunks(values: list[str], count: int = 20):
    for offset in range(0, len(values), count):
        yield values[offset : offset + count]


def unique_file_titles(items: list[dict]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        image = item.get("image")
        title = image.get("file_title") if isinstance(image, dict) else None
        if isinstance(title, str) and title not in seen:
            seen.add(title)
            output.append(title)
    return output


def choose_page(client: WikiClient, kind: str, discovered: list[dict[str, str]],
                errors: list[dict]) -> tuple[str, str]:
    candidates = [e["title"] for e in discovered if e.get("title")] + FALLBACK_PAGES.get(kind, [])
    seen: set[str] = set()
    for title in candidates:
        if title in seen:
            continue
        seen.add(title)
        try:
            parsed = client.parse_page(title, response_name=f"{kind}-{len(seen)}")
            source = parsed.get("text", "")
            if isinstance(source, dict):
                source = source.get("*", "")
            if isinstance(source, str) and source:
                return title, source
        except Exception as exc:  # noqa: BLE001
            errors.append({"stage": f"fetch_{kind}_catalog", "page_title": title,
                           "error": f"{type(exc).__name__}: {exc}"})
    raise RuntimeError(f"no readable {kind} catalog page")


def sync() -> dict:
    MD.mkdir(parents=True, exist_ok=True)
    RESPONSES.mkdir(parents=True, exist_ok=True)
    client = WikiClient(RESPONSES, min_interval=2.0, user_agent="ptn-wallpaper/0.1 read-only")
    errors: list[dict] = []

    home = client.parse_page("首页", response_name="home")
    home_source = home.get("text", "")
    if isinstance(home_source, dict):
        home_source = home_source.get("*", "")
    links = find_catalog_links(str(home_source))

    character_title = str(home.get("title", "首页"))
    character_source = str(home_source)
    characters, issues = parse_characters(character_source, character_title)
    if not characters:
        characters, card_issues = parse_character_cards(character_source, character_title)
        issues.extend(card_issues)

    skin_title, skin_source = choose_page(client, "skins", links["skins"], errors)
    skins, skin_issues = parse_skins(skin_source, skin_title)
    issues.extend(skin_issues)

    image_titles = unique_file_titles(characters + skins)
    imageinfo: dict[str, dict] = {}
    for index, batch in enumerate(chunks(image_titles), 1):
        imageinfo.update(client.imageinfo(batch, response_name=f"imageinfo-{index}"))
    attach_imageinfo(characters, imageinfo)
    attach_imageinfo(skins, imageinfo)

    atomic_write_json(MD / "characters.json", {
        "schema_version": 1, "source_page": character_title, "characters": characters,
    })
    atomic_write_json(MD / "skins.json", {
        "schema_version": 1, "source_page": skin_title, "skins": skins,
    })
    if errors or issues:
        atomic_write_json(MD / "bwiki_sync_issues.json", {"errors": errors, "issues": issues})
    return {"characters": len(characters), "skins": len(skins),
            "issues": len(issues), "errors": len(errors)}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="同步无期迷途 BWiki 图鉴")
    ap.add_argument("--offline", action="store_true", help="仅统计已有数据，不联网")
    args = ap.parse_args()

    if args.offline:
        chars = json.loads((MD / "characters.json").read_text(encoding="utf-8")) if (MD / "characters.json").exists() else {}
        skins = json.loads((MD / "skins.json").read_text(encoding="utf-8")) if (MD / "skins.json").exists() else {}
        print(f"离线统计: 角色 {len(chars.get('characters', []))}, 装束 {len(skins.get('skins', []))}")
        return 0

    stats = sync()
    print(f"同步完成: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
