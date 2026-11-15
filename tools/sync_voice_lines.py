# -*- coding: utf-8 -*-
"""从 BWiki「语音:<角色> <装束名>」页面同步台词文本与语音音频。

数据源结构（已验证）：
    语音页条目形如 {{语音|主体=清风入怀|动作=问候|语音文字=...}}，每条一行；
    音频由 模板:语音 按「主体-动作」拼接为 File:<主体>-<动作>.mp3（wiki File 命名空间），
    真实 URL 经 imageinfo API（patchwiki.biligame.com）获取后下载。

产物：
    resources/voice/<painting>/<动作>.mp3              音频（缺失分类降级为纯台词）
    resources/metadata/voice_lines.json                归一化台词数据（按 painting 索引）
    resources/metadata/voice_lines_issues.json         缺页/解析失败记录

归一化分类：
    greet  ← 问候 / 获得装束 / 获得禁闭者      （入场）
    boring ← 烦躁                              （Boring 动作的回落词，idle 为空时使用）
    idle   ← 闲置                              （Boring 动作配音，优先）
    level  ← 升级                              （点击序列）
    touch  ← 普通触摸N / 摸头 / 特别关注       （点击序列：摸头绑 Click_Head，其余池内轮换）

注意 BWiki 存在限流（实测 HTTP 567/429）：请求间隔 2 秒（AGENTS 铁律 #3）。

用法：
    python tools/sync_voice_lines.py                # 全量同步
    python tools/sync_voice_lines.py 杜若 卓娅      # 仅指定角色
    python tools/sync_voice_lines.py --offline      # 不联网，按本地已有音频重建 audio 标记
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bwiki_lib.client import WikiClient, validate_url  # noqa: E402
from bwiki_lib.io_utils import atomic_write_bytes, atomic_write_json  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "resources" / "metadata"
RESPONSES = MD / "bwiki_responses"
VOICE_DIR = ROOT / "resources" / "voice"

# 台词动作 → 归一化分类
CATEGORY_MAP: list[tuple[str, str]] = [
    ("问候", "greet"),
    ("获得装束", "greet"),
    ("获得禁闭者", "greet"),
    ("烦躁", "boring"),
    ("闲置", "idle"),
    ("升级", "level"),
    ("摸头", "touch"),
    ("特别关注", "touch"),
    # 四星皮二阶段互动（游戏内进入二阶段互动后的动作语音）：
    # 互动1 由 Greet 播放，互动2-5 由其余 Click 轮换，退出互动由 Exitidle 播放。
    ("退出互动", "interact_exit"),
    ("互动", "interact"),
]

# 四星二阶段装束在图鉴里还是占位名时的语音页面映射（键为 painting，改名后依然生效；
# 「· 阶段一/二」后缀的装束名由通用剥离规则处理，无需登记）。
VOICE_NAME_OVERRIDES: dict[tuple[str, str], str] = {
    ("诺克斯", "nuokesi_501"): "画中晨曦",
    ("卓娅", "zoya_501"): "荒雪统御",
    ("哈梅尔", "hameier_501"): "依偎涟漪",
    ("可可莉克", "coquelicot_501"): "绯夜初醒",
    ("瑟琳", "shalom_601"): "微明止息",
}


def categorize(action: str) -> str | None:
    folded = action.strip()
    if folded.startswith("普通触摸"):
        return "touch"
    for prefix, category in CATEGORY_MAP:
        if folded.startswith(prefix):
            return category
    return None


def parse_voice_entries(wikitext: str) -> list[dict]:
    """从 wikitext 解析 {{语音|主体=X|动作=Y|语音文字=Z}} 条目（容错：忽略嵌套花括号）。"""
    # BWiki 模板常在参数里留 <!--禁闭者姓名--> / <!--填写语音文字--> 占位注释。
    # 不剥离会把注释拼进主体名（音频文件名对不上、丢失音频）或台词文本。
    wikitext = re.sub(r"<!--.*?-->", "", wikitext, flags=re.DOTALL)
    entries: list[dict] = []
    for match in re.finditer(r"\{\{\s*语音\s*\|(.*?)\}\}", wikitext, re.DOTALL):
        params: dict[str, str] = {}
        for part in match.group(1).split("|"):
            key, _, value = part.partition("=")
            key = key.strip()
            if key in ("主体", "动作", "语音文字"):
                params[key] = value.strip()
        action = params.get("动作", "")
        text = params.get("语音文字", "")
        if action and text:
            entries.append({"subject": params.get("主体", ""), "action": action, "text": text})
    return entries


def load_skins() -> list[dict]:
    data = json.loads((MD / "skins.json").read_text(encoding="utf-8"))
    return [s for s in data.get("skins", []) if s.get("type") == "live2d" and s.get("painting")]


def fetch_voice_page(client: WikiClient, titles: list[str], errors: list[dict], tag: str) -> tuple[str, list[dict]] | None:
    """依次尝试候选语音页标题，返回 (使用的标题, 条目列表)。全部缺失返回 None。"""
    for title in titles:
        try:
            payload = client.api_get(
                {"action": "parse", "page": title, "prop": "wikitext", "redirects": 1},
                response_name=f"voice-{tag}",
            )
        except Exception as exc:  # noqa: BLE001
            # parse API 对缺失页返回 error，WikiClient 抛 RuntimeError —— 视为该页不存在
            errors.append({"title": title, "error": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        parsed = payload.get("parse", {})
        wikitext = parsed.get("wikitext", "")
        if isinstance(wikitext, dict):
            wikitext = wikitext.get("*", "")
        entries = parse_voice_entries(str(wikitext))
        if entries:
            return str(parsed.get("title", title)), entries
        errors.append({"title": title, "error": "page exists but no 语音 entries"})
    return None


def download_audio(client: WikiClient, file_titles: list[str]) -> dict[str, dict]:
    """批量 imageinfo 解析音频真实 URL。返回 {File标题: {url,size,mime}}（缺失为 {missing: True}）。

    注意 MediaWiki 会把返回 title 的命名空间本地化为「文件:」（而非 File:），
    这里统一剥掉命名空间前缀再以 File: 前缀归一化，避免键不匹配。
    """
    def normalize_key(title: str) -> str:
        name = title.split(":", 1)[-1]
        return f"File:{name}"

    infos: dict[str, dict] = {}
    for offset in range(0, len(file_titles), 20):
        batch = file_titles[offset : offset + 20]
        raw = client.imageinfo(batch, response_name=f"voice-audio-{offset // 20 + 1}")
        infos.update({normalize_key(title): info for title, info in raw.items()})
    return infos


def save_audio(client: WikiClient, painting: str, action: str, info: dict, saved: list[str]) -> str | None:
    """下载单个 mp3 到 resources/voice/<painting>/<动作>.mp3，返回相对路径或 None。"""
    if info.get("missing"):
        return None
    url = info.get("url") or ""
    if not url:
        return None
    try:
        validate_url(url)
    except Exception:  # noqa: BLE001
        return None
    dest_dir = VOICE_DIR / painting
    dest = dest_dir / f"{action}.mp3"
    expected_size = int(info.get("size") or 0)
    # 幂等：本地已有同尺寸文件则跳过下载
    if dest.is_file() and (not expected_size or dest.stat().st_size == expected_size):
        return f"voice/{painting}/{action}.mp3"
    try:
        result = client.fetch(url, response_name=f"voice-mp3-{painting}-{action}", max_bytes=16 * 1024 * 1024)
    except Exception:  # noqa: BLE001
        return None
    data = result.data
    # MP3 合法性：ID3 头或 MPEG 帧同步字
    if not (data.startswith(b"ID3") or (len(data) > 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)):
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(dest, data)
    saved.append(f"{painting}/{action}.mp3")
    return f"voice/{painting}/{action}.mp3"


def normalize(entries: list[dict], painting: str, audio_urls: dict[str, dict],
              client: WikiClient | None, saved: list[str],
              attach_interact: bool = False) -> dict[str, list[dict]]:
    """把 wiki 条目归一化为 {greet, boring, idle, level, touch[, interact, interact_exit]} 结构。

    interact / interact_exit 只在二阶段装束（painting = 阶段一 painting + "01"）上落库：
    游戏里「互动1-5 / 退出互动」是二阶段专属动作语音，一阶段挂上会让运行时误判为二阶段。
    """
    normalized: dict[str, list[dict]] = {
        "greet": [], "boring": [], "idle": [], "level": [], "touch": [],
    }
    if attach_interact:
        normalized["interact"] = []
        normalized["interact_exit"] = []
    for entry in entries:
        category = categorize(entry["action"])
        if not category:
            continue
        if category in ("interact", "interact_exit") and category not in normalized:
            continue
        audio_rel = None
        if client is not None:
            subject = entry.get("subject") or painting
            file_title = f"File:{subject}-{entry['action']}.mp3"
            info = audio_urls.get(file_title) or {}
            audio_rel = save_audio(client, painting, entry["action"], info, saved)
        normalized[category].append({
            "action": entry["action"],
            "text": entry["text"],
            "audio": audio_rel,
        })
    return normalized


def _audio_associations(data: dict) -> set[tuple[str, str, str, str]]:
    output: set[tuple[str, str, str, str]] = set()
    for painting, lines in data.get("skins", {}).items():
        if not isinstance(lines, dict):
            continue
        for category, items in lines.items():
            if not isinstance(items, list):
                continue
            for item in items:
                audio = item.get("audio")
                if audio:
                    output.add((str(painting), str(category), str(item.get("action", "")), str(audio)))
    return output


def cached_voice_pages() -> dict[str, list[dict]]:
    """读取缓存语音页；同标题多份缓存内容必须一致，否则拒绝静默猜。"""
    pages: dict[str, list[dict]] = {}
    signatures: dict[str, str] = {}
    for file in sorted(RESPONSES.glob("*.bin")):
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
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
        if not entries:
            continue
        signature = json.dumps(entries, ensure_ascii=False, sort_keys=True)
        old = signatures.get(title)
        if old is not None and old != signature:
            raise RuntimeError(f"缓存语音页内容冲突，需人工 review: {title}")
        signatures[title] = signature
        pages[title] = entries
    return pages


def migrate_cached_idle() -> dict[str, int]:
    """仅从缓存补充 idle/闲置列表，保证其余数据与既有音频关联不变。"""
    path = MD / "voice_lines.json"
    if not path.is_file():
        raise FileNotFoundError("voice_lines.json 不存在，先联网同步一次")
    data = json.loads(path.read_text(encoding="utf-8"))
    before_audio = _audio_associations(data)
    before_non_idle = {
        painting: {key: value for key, value in lines.items() if key != "idle"}
        for painting, lines in data.get("skins", {}).items()
        if isinstance(lines, dict)
    }
    pages = cached_voice_pages()
    skins_by_painting = {str(s["painting"]): s for s in load_skins()}
    added = 0
    with_idle = 0
    for painting, lines in data.get("skins", {}).items():
        if not isinstance(lines, dict):
            continue
        skin = skins_by_painting.get(str(painting))
        if not skin:
            lines.setdefault("idle", [])
            continue
        character = str(skin.get("character", ""))
        name = str(skin.get("name", ""))
        entries = pages.get(f"语音:{character} {name}")
        if entries is None:
            entries = pages.get(f"语音:{character}", [])
        idle_entries = [entry for entry in entries if categorize(entry.get("action", "")) == "idle"]
        existing = lines.get("idle") if isinstance(lines.get("idle"), list) else []
        by_key = {(str(item.get("action", "")), str(item.get("text", ""))): item for item in existing}
        for entry in idle_entries:
            key = (entry["action"], entry["text"])
            if key in by_key:
                continue
            rel = f"voice/{painting}/{entry['action']}.mp3"
            item = {
                "action": entry["action"],
                "text": entry["text"],
                "audio": rel if (ROOT / "resources" / rel).is_file() else None,
            }
            existing.append(item)
            by_key[key] = item
            added += 1
        lines["idle"] = existing
        if existing:
            with_idle += 1
    after_non_idle = {
        painting: {key: value for key, value in lines.items() if key != "idle"}
        for painting, lines in data.get("skins", {}).items()
        if isinstance(lines, dict)
    }
    if before_non_idle != after_non_idle:
        raise AssertionError("离线迁移意外修改了非 idle 数据")
    if before_audio != _audio_associations(data):
        raise AssertionError("离线迁移意外改变了既有音频关联")
    atomic_write_json(path, data)
    return {"skins": len(data.get("skins", {})), "with_idle": with_idle, "added": added,
            "preserved_audio": len(before_audio), "cached_pages": len(pages)}


def reattach_local_audio(data: dict) -> dict:
    """--offline：按本地已有 mp3 重建 audio 字段，其余保持不变。"""
    for painting, lines in data.get("skins", {}).items():
        for category, items in lines.items():
            if not isinstance(items, list):
                continue  # character / skin / head_click_interact 等标量字段
            for item in items:
                # interact_order 是手工调序的动作名列表，不是台词条目，跳过
                if not isinstance(item, dict):
                    continue
                rel = f"voice/{painting}/{item.get('action', '')}.mp3"
                item["audio"] = rel if (ROOT / "resources" / rel).is_file() else None
    return data


def sync(targets: list[str]) -> dict:
    MD.mkdir(parents=True, exist_ok=True)
    RESPONSES.mkdir(parents=True, exist_ok=True)
    client = WikiClient(
        RESPONSES,
        min_interval=2.0,
        retries=4,
        max_retry_after=120.0,
        user_agent="ptn-wallpaper/0.1 voice-lines read-only",
    )

    existing_path = MD / "voice_lines.json"
    previous: dict = {}
    if existing_path.is_file():
        try:
            previous = json.loads(existing_path.read_text(encoding="utf-8")).get("skins", {})
        except Exception:  # noqa: BLE001
            previous = {}

    skins = load_skins()
    if targets:
        folded = {t.casefold() for t in targets}
        skins = [s for s in skins if str(s.get("character", "")).casefold() in folded]
    errors: list[dict] = []
    saved: list[str] = []
    out_skins: dict = {}
    total = len(skins)

    # 先收集全部语音页条目（限速：每页 1 次 API 请求）
    resolved: list[tuple[dict, list[dict]]] = []
    for index, skin in enumerate(skins, 1):
        character = str(skin.get("character", ""))
        name = str(skin.get("name", ""))
        painting = str(skin.get("painting", ""))
        titles: list[str] = []
        override = VOICE_NAME_OVERRIDES.get((character, painting))
        if override:
            titles.append(f"语音:{character} {override}")
        clean_name = re.sub(r"\s*·\s*阶段[一二12]", "", name).strip()
        if clean_name and clean_name != name:
            titles.append(f"语音:{character} {clean_name}")
        titles.extend([f"语音:{character} {name}", f"语音:{character}"])
        # 去重并保持尝试优先级
        seen_titles: set[str] = set()
        dedup_titles: list[str] = []
        for t in titles:
            if t not in seen_titles:
                seen_titles.add(t)
                dedup_titles.append(t)
        titles = dedup_titles

        # 常服与专属装束重名页去重：同一组 titles 只取第一个成功结果
        found = fetch_voice_page(client, titles, errors, f"{index}-{character}")
        if found is None:
            print(f"[voice] ({index}/{total}) {character}/{name}: 无语音页", flush=True)
            continue
        page_title, entries = found
        print(f"[voice] ({index}/{total}) {character}/{name}: {page_title} {len(entries)} 条", flush=True)
        resolved.append((skin, entries))

    # 收集全部候选音频文件名，一次批量 imageinfo
    audio_urls: dict[str, dict] = {}
    file_titles: list[str] = []
    seen: set[str] = set()
    for skin, entries in resolved:
        fallback_subject = next((e["subject"] for e in entries if e.get("subject")), "")
        for entry in entries:
            # normalize() 按每条自己的主体查文件名，这里必须同口径，否则集体错位取不到音频
            subject = entry.get("subject") or fallback_subject
            if not subject:
                continue
            title = f"File:{subject}-{entry['action']}.mp3"
            if title not in seen:
                seen.add(title)
                file_titles.append(title)
    if file_titles:
        try:
            audio_urls = download_audio(client, file_titles)
        except Exception as exc:  # noqa: BLE001
            # 音频 URL 解析失败不影响台词文本：降级为纯台词
            print(f"[voice] 音频 URL 解析失败（降级为纯台词）: {exc}", flush=True)
            errors.append({"stage": "imageinfo", "error": f"{type(exc).__name__}: {exc}"[:200]})

    # 归一化 + 下载音频（mp3 下载沿用同一 client 限速）
    # 二阶段识别：painting = 阶段一 painting + "01"（如 nuokesi_5 → nuokesi_501、
    # shalom_6 → shalom_601）。互动1-5/退出互动只落在二阶段装束上。
    all_paintings = {str(s.get("painting", "")) for s in load_skins()}
    stage2_paintings = {p for p in all_paintings if p.endswith("01") and p[:-2] in all_paintings}
    # 装束级手工标记（如 head_click_interact）不属于 wiki 数据，重同步时必须保留；
    # interact_order 是手工调的互动轮换顺序（如画中晨曦二阶段把互动2/3 对调），
    # 重同步后按它把 interact 池重新排回。
    MANUAL_FLAGS = ("head_click_interact",)
    for skin, entries in resolved:
        painting = str(skin["painting"])
        out_skins[painting] = {
            "character": skin.get("character", ""),
            "skin": skin.get("name", ""),
            **normalize(entries, painting, audio_urls, client, saved,
                        attach_interact=painting in stage2_paintings),
        }
        prev_entry = previous.get(painting) or {}
        for flag in MANUAL_FLAGS:
            if flag in prev_entry:
                out_skins[painting][flag] = prev_entry[flag]
        order = prev_entry.get("interact_order")
        pool = out_skins[painting].get("interact")
        if isinstance(order, list) and isinstance(pool, list):
            by_action = {item["action"]: item for item in pool}
            ordered = [by_action[action] for action in order if action in by_action]
            ordered += [item for item in pool if item["action"] not in order]
            out_skins[painting]["interact"] = ordered
            out_skins[painting]["interact_order"] = order

    # 简单合并：本次成功解析的装束覆盖旧数据；解析失败（限流/缺页）的保留旧数据，
    # 避免限流期间一轮全失败的同步把整个台词库清空。
    payload = {
        "schema_version": 1,
        "skins": {**previous, **out_skins},
    }
    atomic_write_json(MD / "voice_lines.json", payload)
    if errors:
        atomic_write_json(MD / "voice_lines_issues.json", {"errors": errors[:400]})
    print(
        f"[voice] 同步完成: 装束 {len(out_skins)}/{total} 有台词, 音频新增 {len(saved)}, "
        f"缺页/异常 {len(errors)}",
        flush=True,
    )
    return {"skins": len(out_skins), "total": total, "audio": len(saved), "errors": len(errors)}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="同步无期迷途 BWiki 语音台词")
    ap.add_argument("characters", nargs="*", help="仅同步指定角色（默认全量）")
    ap.add_argument("--offline", action="store_true", help="不联网：按本地已有 mp3 重建 audio 标记")
    ap.add_argument("--migrate-idle-cache", action="store_true",
                    help="不联网：从缓存语音页只补 idle/闲置分类，保留其余数据")
    args = ap.parse_args()

    if args.migrate_idle_cache:
        stats = migrate_cached_idle()
        print(f"闲置缓存迁移完成: {stats}")
        return 0

    if args.offline:
        path = MD / "voice_lines.json"
        if not path.is_file():
            print("voice_lines.json 不存在，先联网同步一次")
            return 1
        data = json.loads(path.read_text(encoding="utf-8"))
        reattach_local_audio(data)
        atomic_write_json(path, data)
        print("离线重建完成")
        return 0

    stats = sync(args.characters)
    print(f"同步完成: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
