# -*- coding: utf-8 -*-
"""构建无期迷途元数据：把本地资源扫描结果与 BWiki 图鉴合并。

匹配规则（2026-11 实测确立）：
    资源立绘命名  lihui_{角色拼音或英文名}_{皮肤拼音或英文名}[_序号]
    角色匹配      资源前缀 == pypinyin(中文名) 或 英文名（大小写/空格归一后前缀匹配）
    英文名来源    BWiki 角色详情页（sync_bwiki_catalog 后续版本采集，见 en_aliases）

无法自动匹配的前缀写入 review 列表，绝不静默猜测归属。

用法：
    python tools/build_ptn_metadata.py                 # 扫描 resources/bundles + 合并
    python tools/build_ptn_metadata.py --names x.json  # 用缓存的资源名列表（调试用）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "resources" / "metadata"
BUNDLES = ROOT / "resources" / "bundles"

try:
    from pypinyin import lazy_pinyin
except ImportError:  # noqa: BLE001
    lazy_pinyin = None


def norm_en(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", value or "").lower())


def pinyin_key(cn_name: str) -> str:
    if not lazy_pinyin:
        return ""
    return "".join(lazy_pinyin(cn_name)).lower()


def load_en_aliases() -> dict[str, str]:
    """中文角色名 -> 资源内部名。来源：PtNData 社区数据（Internal Name 字段）
    转换脚本见 tools/build_name_aliases.py；BWiki 页面采集可作为补充校验。"""
    for name in ("name_aliases.json", "en_aliases.json"):
        path = MD / name
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def scan_resource_names(bundles_dir: Path) -> set[str]:
    """扫描拆分后的 UnityFS，收集 lihui_* 资源名。"""
    import UnityPy  # 延迟导入

    names: set[str] = set()
    for f in sorted(bundles_dir.rglob("*.unityfs")):
        try:
            env = UnityPy.load(str(f))
        except Exception:  # noqa: BLE001
            continue
        for obj in env.objects:
            if obj.type.name not in ("Texture2D", "Sprite"):
                continue
            try:
                name = obj.read().m_Name or ""
            except Exception:  # noqa: BLE001
                continue
            if name.startswith("lihui"):
                names.add(name)
    return names


def build(characters: list[dict], resource_names: set[str]) -> dict:
    aliases = load_en_aliases()
    keys: dict[str, str] = {}
    for c in characters:
        name = str(c.get("name", ""))
        for k in (pinyin_key(name), norm_en(str(aliases.get(name, "")))):
            if k:
                keys.setdefault(k, name)

    matched: dict[str, list[dict]] = {}
    review: dict[str, int] = {}
    for res in sorted(resource_names):
        m = re.match(r"^lihui_([a-zA-Z0-9]+?)_(.+)$", res)
        if not m:
            review[res] = review.get(res, 0) + 1
            continue
        prefix, skin_part = m.group(1), m.group(2)
        char = keys.get(prefix.lower())
        if char is None:
            # 英文名允许资源用缩写前缀（如 oak ⊂ oakcasket）
            for k, cn in keys.items():
                if k.startswith(prefix.lower()):
                    char = cn
                    break
        if char is None:
            review[prefix] = review.get(prefix, 0) + 1
            continue
        matched.setdefault(char, []).append(
            {"resource": res, "skin_key": skin_part}
        )

    return {
        "resource_names": len(resource_names),
        "matched_characters": len(matched),
        "matched_resources": sum(len(v) for v in matched.values()),
        "review_prefixes": dict(sorted(review.items(), key=lambda kv: -kv[1])),
        "matchings": {k: sorted(v, key=lambda x: x["resource"])
                      for k, v in sorted(matched.items())},
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="合并资源扫描与 BWiki 图鉴")
    ap.add_argument("--names", help="缓存的资源名 JSON（scan_resource_names 产物）")
    ap.add_argument("--emit-scan-cache", help="扫描结果写到此 JSON 后退出")
    args = ap.parse_args()

    chars_path = MD / "characters.json"
    if not chars_path.exists():
        print("缺少 resources/metadata/characters.json，请先运行 sync_bwiki_catalog.py")
        return 2
    characters = json.loads(chars_path.read_text(encoding="utf-8"))["characters"]

    if args.names:
        resource_names = set(json.loads(Path(args.names).read_text(encoding="utf-8")))
    else:
        if not BUNDLES.exists():
            print("resources/bundles 不存在")
            return 2
        resource_names = scan_resource_names(BUNDLES)
        if args.emit_scan_cache:
            Path(args.emit_scan_cache).write_text(
                json.dumps(sorted(resource_names), ensure_ascii=False, indent=1),
                encoding="utf-8",
            )

    report = build(characters, resource_names)
    out = MD / "resource_matching.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"资源名: {report['resource_names']}")
    print(f"匹配角色: {report['matched_characters']}/{len(characters)}")
    print(f"匹配资源: {report['matched_resources']}")
    print(f"待 review 前缀: {len(report['review_prefixes'])} 个 "
          f"(top: {list(report['review_prefixes'].items())[:5]})")
    print(f"明细: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
