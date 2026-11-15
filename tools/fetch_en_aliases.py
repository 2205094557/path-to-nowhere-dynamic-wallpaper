# -*- coding: utf-8 -*-
"""从 BWiki 角色详情页采集官方英文名，生成 en_aliases.json。

用于 tools/build_ptn_metadata.py 的资源名匹配（资源立绘用英文名/拼音命名）。
限速 2 秒/请求；支持断点续跑（已有条目跳过，--force 重采）。

用法：
    python tools/fetch_en_aliases.py [--force]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bwiki_lib.client import WikiClient  # noqa: E402
from bwiki_lib.io_utils import atomic_write_json  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "resources" / "metadata"
RESPONSES = MD / "bwiki_responses"

# 角色页信息卡中英文名的常见标签形态
EN_PATTERNS = (
    re.compile(r"英文[^<>]{0,6}</th>\s*<td[^>]*>([^<]{1,40})</td>"),
    re.compile(r"英文名[^<>]{0,6}</th>\s*<td[^>]*>([^<]{1,40})</td>"),
    re.compile(r'infobox[^>]*>.*?英文[^<>]{0,6}</th>\s*<td[^>]*>([^<]{1,40})</td>', re.S),
)


def extract_en_name(html: str) -> str | None:
    for pat in EN_PATTERNS:
        m = pat.search(html)
        if m:
            value = m.group(1).strip()
            # 只接受拉丁字母/空格/点，排除误抓
            if re.fullmatch(r"[A-Za-z][A-Za-z .'\-]{0,38}", value):
                return value
    return None


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="采集角色英文名")
    ap.add_argument("--force", action="store_true", help="忽略缓存全部重采")
    args = ap.parse_args()

    chars_path = MD / "characters.json"
    if not chars_path.exists():
        print("缺少 characters.json，请先运行 sync_bwiki_catalog.py")
        return 2
    characters = json.loads(chars_path.read_text(encoding="utf-8"))["characters"]

    aliases_path = MD / "en_aliases.json"
    aliases: dict[str, str] = {}
    if aliases_path.exists() and not args.force:
        aliases = json.loads(aliases_path.read_text(encoding="utf-8"))

    RESPONSES.mkdir(parents=True, exist_ok=True)
    client = WikiClient(RESPONSES, min_interval=5.0, retries=1, user_agent="ptn-wallpaper/0.1 read-only")

    todo = [c["name"] for c in characters
            if isinstance(c.get("name"), str) and (args.force or c["name"] not in aliases)]
    print(f"待采集: {len(todo)}/{len(characters)}")
    done = 0
    for i, name in enumerate(todo, 1):
        title = f"禁闭者:{name}"
        en = None
        for attempt in range(5):
            try:
                parsed = client.parse_page(title, response_name=f"char-{i % 3}")
                html = parsed.get("text", "")
                if isinstance(html, dict):
                    html = html.get("*", "")
                en = extract_en_name(str(html))
                break
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if "567" in msg or "429" in msg:
                    wait = 120 * (attempt + 1)
                    print(f"  [{i}/{len(todo)}] {name}: 限流，退避 {wait}s")
                    time.sleep(wait)
                    continue
                print(f"  [{i}/{len(todo)}] {name}: {type(e).__name__}: {msg[:80]}")
                break
        if en:
            aliases[name] = en
            done += 1
        if i % 20 == 0:
            atomic_write_json(aliases_path, aliases)
            print(f"  进度 {i}/{len(todo)}，已采集 {done}")
    atomic_write_json(aliases_path, aliases)
    print(f"完成: 采集 {done} 个英文名，共 {len(aliases)} 条 -> {aliases_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
