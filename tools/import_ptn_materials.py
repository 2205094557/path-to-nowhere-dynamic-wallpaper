# -*- coding: utf-8 -*-
"""从模型 prefab bundle 提取 CubismRenderControllerV2 的 materialIds，
转换为标准 PTN 材质索引并写入各模型 model3.json 的 PTNRender 字段。

数据链路：
  资源索引.jsonl 找到 build_prefabs_live2d_characters_char2d_{model} bundle
  → 工作数据 Unity 缓存里的 CAB 文件
  → CubismRenderControllerV2.materialIds（prefab 材质表顺序）
  → 按材质名转换为标准 PTN 顺序（UnlitMaskedInverted/Unlit/Multiply/...）
  → model3.json 的 PTNRender.MaterialIds + MaterialNames

用法：
  python tools/import_ptn_materials.py            # 批量处理全部模型
  python tools/import_ptn_materials.py Nuokesi_2  # 只处理指定模型
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "1111111" / "工作数据"
L2D = ROOT / "resources" / "extracted" / "live2d"

# 标准 PTN 材质顺序（与 ptn_render_profiles.json / 前端 modes 表一致）
STANDARD_NAMES = [
    "UnlitMaskedInverted",      # 0 -> Cubism flag 8 (InvertedMask)
    "Unlit",                    # 1 -> 0 (Normal)
    "UnlitMultiply",            # 2 -> 2 (Multiplicative)
    "UnlitMasked",              # 3 -> 0（遮罩由 moc 自带 mask 数据处理）
    "UnlitAdditive",            # 4 -> 1 (Additive)
    "UnlitAdditiveMaskedInverted",  # 5 -> 9 (Additive+InvertedMask)
    "UnlitMultiplyMasked",      # 6 -> 2
    "UnlitAdditiveMasked",      # 7 -> 1
    "Mask",                     # 8 -> 1
]
NAME_TO_STD = {name: i for i, name in enumerate(STANDARD_NAMES)}


def find_prefab_cab(model: str) -> tuple[str, str] | None:
    """在资源索引里找模型 prefab bundle 的 CAB 名。匹配 char2d_{model 小写}。"""
    target = f"prefabs/live2d/characters/char2d_{model.lower()}"
    idx_path = None
    for cand in WORK.rglob("资源索引.jsonl"):
        idx_path = cand
        break
    if idx_path is None:
        return None
    with idx_path.open(encoding="utf-8") as f:
        for line in f:
            if "build_prefabs_live2d_characters" not in line:
                continue
            if target not in line:
                continue
            row = json.loads(line)
            for n in row["nodes"]:
                names = n.get("names", [])
                if any(x == target for x in names):
                    cab = n.get("cab", "")
                    return cab, str(idx_path.parent / "Unity" / cab)
    return None


def extract_material_ids(cab_path: str) -> tuple[list[int], list[str]] | None:
    """解析 prefab 里 RenderControllerV2 的 materialIds，返回（标准索引数组, 材质名表）。"""
    import UnityPy

    env = UnityPy.load(cab_path)

    # 1. 材质 pathid -> 名字
    mat_names: dict[int, str] = {}
    for o in env.objects:
        if o.type.name == "Material":
            d = o.read()
            mat_names[o.path_id] = d.m_Name

    # 2. RenderControllerV2 的 materialIds（按 prefab materials 数组顺序）
    prefab_order: list[str] = []   # prefab materials 数组对应的材质名
    raw_ids: list[int] = []
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            tt = o.read_typetree()
        except Exception:  # noqa: BLE001
            continue
        if "materialIds" not in tt:
            continue
        for mref in tt.get("materials", []):
            pid = mref.get("m_PathID", 0)
            prefab_order.append(mat_names.get(pid, "Unlit"))
        raw_ids = [int(x) for x in tt["materialIds"]]
        break

    if not raw_ids:
        return None

    # 3. prefab 顺序 -> 标准顺序索引
    std_map = [NAME_TO_STD.get(n, 1) for n in prefab_order]
    converted = [std_map[i] if i < len(std_map) else 1 for i in raw_ids]
    return converted, prefab_order


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    ok = fail = skip = 0
    for m3 in sorted(L2D.glob("*/*.model3.json")):
        model_dir = m3.parent
        model = m3.name[: -len(".model3.json")]
        if only and model.casefold() != only.casefold():
            continue
        # 已有 PTNRender 且包含 MaterialIds 的跳过（旧 CDN 管线的模型已带 profile）
        data = json.loads(m3.read_text(encoding="utf-8"))
        if data.get("PTNRender", {}).get("MaterialIds"):
            skip += 1
            continue
        found = find_prefab_cab(model)
        if not found:
            print(f"[SKIP] {model}: prefab bundle 未找到")
            fail += 1
            continue
        cab, cab_path = found
        if not Path(cab_path).exists():
            print(f"[SKIP] {model}: Unity 缓存缺失 {cab}")
            fail += 1
            continue
        result = extract_material_ids(cab_path)
        if not result:
            print(f"[FAIL] {model}: prefab 里没有 materialIds")
            fail += 1
            continue
        ids, order = result
        # drawable 数校验
        moc_ok = True
        try:
            import ctypes
            dll_path = str(ROOT / "无期迷途" / "Live2DCubismCore.dll")
            core = ctypes.cdll.LoadLibrary(dll_path)
            core.csmReviveMocInPlace.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_uint32]
            core.csmReviveMocInPlace.restype = ctypes.c_void_p
            core.csmGetSizeofModel.argtypes = [ctypes.c_void_p]
            core.csmGetSizeofModel.restype = ctypes.c_uint32
            core.csmGetDrawableCount.argtypes = [ctypes.c_void_p]
            core.csmGetDrawableCount.restype = ctypes.c_int
            moc_file = next(model_dir.glob("*.moc3"), None)
            mdata = moc_file.read_bytes()
            buf = ctypes.create_string_buffer(len(mdata) + 64)
            addr = (ctypes.addressof(buf) + 63) & ~63
            ctypes.memmove(addr, mdata, len(mdata))
            moc = core.csmReviveMocInPlace(ctypes.cast(addr, ctypes.POINTER(ctypes.c_ubyte)), len(mdata))
            msize = core.csmGetSizeofModel(moc)
            mbuf = ctypes.create_string_buffer(msize + 16)
            maddr = (ctypes.addressof(mbuf) + 15) & ~15
            mobj = core.csmInitializeModelInPlace(moc, ctypes.c_void_p(maddr), msize)
            n = core.csmGetDrawableCount(mobj)
            if n != len(ids):
                print(f"[FAIL] {model}: materialIds {len(ids)} != drawables {n}")
                fail += 1
                continue
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] {model}: drawable 数校验失败({e})，仍然写入")
        data["PTNRender"] = {
            "MaterialIds": ids,
            "MaterialNames": STANDARD_NAMES,
        }
        m3.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        ok += 1
        print(f"[OK] {model}: materialIds x{len(ids)} (prefab 顺序: {order[:4]}...)")
    print(f"\n完成: 写入 {ok}, 跳过(已有) {skip}, 失败 {fail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
