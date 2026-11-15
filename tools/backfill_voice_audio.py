# -*- coding: utf-8 -*-
"""快速补齐语音音频：跳过页面重新解析，直接用本地缓存的响应。

流程：
    1. 扫描 bwiki_responses/ 里缓存的 parse 响应 → 取「语音:」页 wikitext → 得到
       每条台词的 主体/动作/文本（不再访问网络解析页面）
    2. 与 voice_lines.json 的台词按 (动作, 文本) 匹配 → 拼出 File:{主体}-{动作}.mp3
    3. 批量 imageinfo（20 个/请求）拿真实 URL（patchwiki.biligame.com）
    4. 只下载本地缺失的 mp3，限速 1s/请求（AGENTS 铁律 #3 下限）

用法：
    python tools/backfill_voice_audio.py            # 全量补音频
    python tools/backfill_voice_audio.py 杜若 卓娅   # 仅指定角色
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bwiki_lib.client import WikiClient  # noqa: E402
from bwiki_lib.io_utils import atomic_write_bytes, atomic_write_json  # noqa: E402
from sync_voice_lines import parse_voice_entries  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "resources" / "metadata"
RESPONSES = MD / "bwiki_responses"
VOICE_DIR = ROOT / "resources" / "voice"


def normalize_file_title(title: str) -> str:
    return f"File:{str(title).split(':', 1)[-1]}"


def scan_cached_imageinfo() -> dict[str, dict]:
    """复用历史 imageinfo 响应，避免为已知音频再次请求 BWiki API。"""
    output: dict[str, dict] = {}
    for file in sorted(RESPONSES.glob("*.bin")):
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        query = payload.get("query") if isinstance(payload, dict) else None
        if not isinstance(query, dict):
            continue
        pages = query.get("pages", [])
        if isinstance(pages, dict):
            pages = list(pages.values())
        for page in pages if isinstance(pages, list) else []:
            if not isinstance(page, dict):
                continue
            title = page.get("title")
            infos = page.get("imageinfo")
            if not title or not isinstance(infos, list) or not infos or not isinstance(infos[0], dict):
                continue
            output[normalize_file_title(str(title))] = infos[0]
    return output


def scan_cached_voice_pages() -> dict[str, list[dict]]:
    """扫描缓存的 parse 响应，返回 {页面标题: [entries]}（含主体/动作/文本）。"""
    pages: dict[str, list[dict]] = {}
    for f in sorted(RESPONSES.glob("*.bin")):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        parsed = payload.get("parse") if isinstance(payload, dict) else None
        if not isinstance(parsed, dict):
            continue
        title = str(parsed.get("title", ""))
        if not title.startswith("语音:"):
            continue
        wikitext = parsed.get("wikitext", "")
        if isinstance(wikitext, dict):
            wikitext = wikitext.get("*", "")
        entries = parse_voice_entries(str(wikitext))
        if entries:
            pages[title] = entries
    return pages


def build_painting_subjects(skins: list[dict], cached_pages: dict) -> dict[str, dict[str, str]]:
    """建立 {painting: {(动作, 文本): 主体}} 映射。"""
    title_to_paintings: dict[str, list[str]] = {}
    costume_titles: set[str] = set()
    for skin in skins:
        painting = str(skin["painting"])
        character = str(skin.get("character", ""))
        name = str(skin.get("name", ""))
        costume_title = f"语音:{character} {name}"
        char_title = f"语音:{character}"
        title_to_paintings.setdefault(costume_title, []).append(painting)
        costume_titles.add(costume_title)
        title_to_paintings.setdefault(char_title, []).append(painting)
    # 角色级页只兜底「没有专属语音页命中」的装束
    out: dict[str, dict[str, str]] = {}
    for title, entries in cached_pages.items():
        for painting in title_to_paintings.get(title, []):
            m = out.setdefault(painting, {})
            for e in entries:
                m.setdefault((e["action"], e["text"]), e["subject"])
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    targets = [a for a in sys.argv[1:] if not a.startswith("--")]
    folded = {t.casefold() for t in targets}

    client = WikiClient(
        RESPONSES,
        min_interval=1.0,
        retries=4,
        max_retry_after=120.0,
        user_agent="ptn-wallpaper/0.1 voice-audio-backfill read-only",
    )

    data = json.loads((MD / "voice_lines.json").read_text(encoding="utf-8"))
    skins = load_skins_all()
    cached_pages = scan_cached_voice_pages()
    print(f"[backfill] 缓存语音页 {len(cached_pages)} 个")

    subj_map = build_painting_subjects(skins, cached_pages)

    # 收集缺失音频的条目 → 文件标题
    file_titles: list[str] = []
    seen: set[str] = set()
    pending: list[tuple[str, dict]] = []  # (painting, item)
    for painting, lines in data.get("skins", {}).items():
        character = str(lines.get("character", ""))
        if folded and character.casefold() not in folded:
            continue
        subj = subj_map.get(painting, {})
        for category, items in lines.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if item.get("audio"):
                    continue
                key = (item.get("action", ""), item.get("text", ""))
                subject = subj.get(key, "")
                if not subject:
                    continue
                ft = f"File:{subject}-{item['action']}.mp3"
                if ft not in seen:
                    seen.add(ft)
                    file_titles.append(ft)
                pending.append((painting, item, ft))
    pending = [(p, it, ft) for (p, it, ft) in pending]
    print(f"[backfill] 缺音频台词 {len(pending)} 条，候选文件 {len(file_titles)} 个")

    # 优先复用缓存 imageinfo；只请求缓存中缺失的标题。
    infos: dict[str, dict] = scan_cached_imageinfo()
    missing_titles = [title for title in file_titles if title not in infos]
    print(f"[backfill] 缓存 imageinfo 命中 {len(file_titles) - len(missing_titles)}/{len(file_titles)}，"
          f"待请求 {len(missing_titles)}", flush=True)
    for offset in range(0, len(missing_titles), 20):
        batch = missing_titles[offset : offset + 20]
        try:
            raw = client.imageinfo(batch, response_name=f"backfill-info-{offset // 20 + 1}")
        except Exception as exc:  # noqa: BLE001
            print(f"[backfill] imageinfo 批次失败（跳过）: {exc}")
            continue
        for title, info in raw.items():
            infos[normalize_file_title(title)] = info
        done = min(offset + 20, len(missing_titles))
        print(f"[backfill] imageinfo {done}/{len(missing_titles)}", flush=True)

    # 下载缺失 mp3（1s/请求）：同一文件可能对应多个装束目录，一次下载写全
    by_ft: dict[str, list[tuple[str, dict]]] = {}
    for painting, item, ft in pending:
        by_ft.setdefault(ft, []).append((painting, item))

    saved = 0
    for n, (ft, group) in enumerate(by_ft.items(), 1):
        info = infos.get(ft) or {}
        if info.get("missing") or not info.get("url"):
            continue
        expected = int(info.get("size") or 0)
        # 任一装束目录已有同尺寸文件则不再下载，直接复用字节
        data_bytes = None
        for painting, item in group:
            action = item["action"]
            dest = VOICE_DIR / painting / f"{action}.mp3"
            if dest.is_file() and (not expected or dest.stat().st_size == expected):
                data_bytes = dest.read_bytes()
                break
        if data_bytes is None:
            try:
                result = client.fetch(info["url"], response_name=f"backfill-mp3-{n}", max_bytes=16 * 1024 * 1024)
            except Exception:  # noqa: BLE001
                continue
            d = result.data
            if not (d.startswith(b"ID3") or (len(d) > 2 and d[0] == 0xFF and (d[1] & 0xE0) == 0xE0)):
                continue
            data_bytes = d
            saved += 1
            if saved % 25 == 0:
                print(f"[backfill] 已下载 {saved} 个 mp3", flush=True)
        for painting, item in group:
            action = item["action"]
            dest = VOICE_DIR / painting / f"{action}.mp3"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.is_file() or dest.stat().st_size != len(data_bytes):
                atomic_write_bytes(dest, data_bytes)
            item["audio"] = f"voice/{painting}/{action}.mp3"
        if n % 50 == 0:
            print(f"[backfill] 进度 {n}/{len(by_ft)} 文件", flush=True)

    atomic_write_json(MD / "voice_lines.json", data)
    total_audio = sum(
        1 for lines in data.get("skins", {}).values()
        for cat in lines.values() if isinstance(cat, list)
        for it in cat if it.get("audio")
    )
    print(f"[backfill] 完成：新下载 {saved} 个 mp3，总计带音频台词 {total_audio} 条", flush=True)
    return 0


def load_skins_all() -> list[dict]:
    data = json.loads((MD / "skins.json").read_text(encoding="utf-8"))
    return [s for s in data.get("skins", []) if s.get("type") == "live2d" and s.get("painting")]


if __name__ == "__main__":
    raise SystemExit(main())
