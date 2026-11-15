# -*- coding: utf-8 -*-
"""生成 Live2D 家族 bundle 定位索引（跨 chunk 补取用）。

PTN 的 Live2D 家族（moc / 动画 / 材质 / 特效 / 贴图）可能被拆进不同 chunk：
例如 Hameier_5 的 moc+动画在 chunk A，贴图+材质+特效在 chunk B。按 moc 所在
chunk 装配时就找不到 texture_* 贴图，下载会失败。

本工具从**解密后的 chunk 清单**（客户端 device 明文 res_chunk_manifest.json，
含每个 bundle 的 ChunkIndex/Offset/Length）反查家族成员，输出
resources/metadata/live2d_family_bundles.json：只记录不在 moc chunk 里的
「额外 bundle」，下载时用 HTTP Range 精确补取，避免整包下载另一个 chunk。

bundle 路径哈希（官方清单里的 /assets/xx/<id>.bundle）经验公式：
    id = md5(bundleName)[4:20]
已对 moc / animation / material / effect / texture 五类命名实测一致。

用法：
    python tools/build_live2d_family_index.py <res_chunk_manifest.json> [-o 输出]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "resources" / "metadata"

TEXTURE_RESOLUTIONS = (512, 1024, 2048, 4096)
KINDS = ("animation", "material", "effect")


def _path_id(bundle_name: str) -> str:
    """bundle 名前缀 → 官方清单里的 /assets/<id>.bundle 中的 id。"""
    return hashlib.md5(bundle_name.encode("utf-8")).hexdigest()[4:20]


def _path(bundle_name: str) -> str:
    digest = _path_id(bundle_name)
    return f"assets/{digest[:2]}/{digest}.bundle"


class Manifest:
    def __init__(self, data: dict) -> None:
        self._chunks = data.get("ChunkPatches") or []
        self._by_path = {
            str(entry.get("Path") or ""): entry
            for entry in (data.get("Entries") or [])
            if isinstance(entry, dict)
        }

    def lookup(self, bundle_name: str) -> dict | None:
        entry = self._by_path.get(_path(bundle_name))
        if not entry:
            return None
        index = int(entry.get("ChunkIndex") or 0)
        if not (0 <= index < len(self._chunks)):
            return None
        chunk = self._chunks[index]
        return {
            "name": bundle_name,
            "chunk": str(chunk.get("Filename") or ""),
            "offset": int(entry.get("Offset") or 0),
            "length": int(entry.get("Length") or 0),
        }


def _family_bundles(model: str, moc_name: str) -> list[str]:
    """按家族前缀推导全部成员 bundle 名（贴图名会重复一次模型名）。"""
    stem = moc_name[len("assets/"):-len(".bundle")]
    lower = model.lower()
    names = [f"assets/{stem}_{kind}.bundle" for kind in KINDS]
    for resolution in TEXTURE_RESOLUTIONS:
        names.append(f"assets/{stem}_{lower}.{resolution}.bundle")
    return names


def _moc_bundle_name(manifest: Manifest, model: str) -> str | None:
    lower = model.lower()
    for name in (
        f"assets/art_live2d_characters_{lower}.bundle",
        f"assets/art_live2d_{lower}.bundle",
    ):
        if manifest.lookup(name):
            return name
    return None


def build(manifest: Manifest, state: dict) -> dict:
    models: dict[str, dict] = {}
    for model in sorted(state.get("moc3_bundles") or {}):
        moc_name = _moc_bundle_name(manifest, model)
        if moc_name is None:
            continue  # 非 characters 命名（剧情/场景模型），无跨 chunk 家族可推导
        moc = manifest.lookup(moc_name)
        if moc is None:
            continue
        extras = []
        for name in _family_bundles(model, moc_name):
            found = manifest.lookup(name)
            if found and found["chunk"] != moc["chunk"]:
                extras.append(found)
        if extras:
            models[model] = {"chunk": moc["chunk"], "bundles": extras}
    return {"schema": 1, "models": models}


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 Live2D 家族跨 chunk 索引")
    ap.add_argument("manifest", help="解密后的 res_chunk_manifest.json")
    ap.add_argument("-o", "--output", default=str(MD / "live2d_family_bundles.json"))
    ap.add_argument("--state", default=str(MD / "library_state.json"))
    args = ap.parse_args()

    manifest = Manifest(json.loads(Path(args.manifest).read_text(encoding="utf-8")))
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    payload = build(manifest, state)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    for model, entry in payload["models"].items():
        total = sum(item["length"] for item in entry["bundles"])
        print(f"{model}: {len(entry['bundles'])} 个额外 bundle, {total / 1048576:.1f} MiB")
    print(f"已写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
