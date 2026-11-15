# -*- coding: utf-8 -*-
"""从 MuMu 模拟器「一键导出最新/缺失的 Live2D 装束」。

只拉需要的文件，不搬 17GB 全量：

    1. adb 连接设备，拉取设备上的明文清单（fs/ + fsclassify/，合计几 MB）
    2. 用清单判断哪些装束「本地缺失」且「设备清单里有对应组」
    3. 按组只拉该组的 bundle 文件（fsclassify/assets/xx/hash.bundle，通常 5 个）
    4. 复用 tools/assemble_classify 的装配管线导出并规范化动作名
    5. 重建本地索引 / 图鉴挂接，并可选把新组合并进 classify 索引

用法：
    python tools/export_from_emulator.py --dry-run        # 只体检，不下载
    python tools/export_from_emulator.py                  # 默认：导出全部缺失装束
    python tools/export_from_emulator.py --limit 3        # 只导最新 3 个
    python tools/export_from_emulator.py --painting coquelicot_5 hameier_5
    python tools/export_from_emulator.py --device-files D:/dump/files   # 不连 adb

说明：设备清单必须是明文（游戏更新后会写入 /sdcard/Android/data/<包名>/files/）。
若清单缺失或仍是密文，请先在游戏里完成一次资源更新再重试。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MD = ROOT / "resources" / "metadata"
EXTRACTED = ROOT / "resources" / "extracted"
WORK = ROOT / "resources" / "bundles" / "emulator_pull"
DEFAULT_ADB = Path(r"D:/Program Files/Netease/MuMu/nx_main/adb.exe")
DEFAULT_PACKAGE = "com.zy.wqmt.cn"

# 所有临时/工作数据强制落在项目所在盘（D:），避免系统临时目录（C:）被大文件占满。
_TMP_DIR = ROOT / "resources" / "bundles" / "_tmp"
_TMP_DIR.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = str(_TMP_DIR)
os.environ["TEMP"] = str(_TMP_DIR)
os.environ["TMPDIR"] = str(_TMP_DIR)
tempfile.tempdir = str(_TMP_DIR)

CONNECT_CANDIDATES = ("127.0.0.1:16384", "127.0.0.1:7555")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


# ---------------------------------------------------------------- 清单定位

def _read_json(path: Path) -> dict | list:
    """读取明文清单；密文时给出明确提示。"""
    raw = path.read_bytes()
    head = raw[:1]
    if head not in (b"{", b"["):
        raise RuntimeError(
            f"{path.name} 不是明文清单（首字节 0x{raw[0]:02x}）。"
            "请先在游戏客户端完成一次资源更新，再重新拉取。"
        )
    return json.loads(raw)


def load_catalog(files_root: Path) -> dict:
    """读取设备清单，返回 {groups, infos, slices, delete_ids, setting_ids}。"""
    candidates = {
        "groups": [files_root / "fs" / "res_base_classify.json",
                   files_root / "fsclassify" / "res_base_classify.json",
                   files_root / "res_base_classify.json"],
        "flat": [files_root / "fs" / "res_classify.json",
                 files_root / "fsclassify" / "res_classify.json",
                 files_root / "res_classify.json"],
        "slices": [files_root / "fsclassify" / "res_install.json",
                   files_root / "fs" / "res_install.json",
                   files_root / "res_install.json"],
        "delete": [files_root / "fsclassify" / "res_base_classify_delete.json",
                   files_root / "res_base_classify_delete.json"],
        "setting": [files_root / "fsclassify" / "res_classify_download_setting.json",
                    files_root / "res_classify_download_setting.json"],
    }

    def first(key: str):
        for path in candidates[key]:
            if path.is_file():
                return _read_json(path)
        return None

    groups_raw = first("groups")
    if not isinstance(groups_raw, dict) or "Live2d" not in groups_raw:
        raise RuntimeError("未找到有效的 res_base_classify.json（缺少 Live2d 分类）")
    flat_raw = first("flat")
    if isinstance(flat_raw, dict):
        infos = flat_raw.get("Infos") or []
    else:
        infos = flat_raw or []
    slices = first("slices") or []
    delete_raw = first("delete") or {}
    setting_raw = first("setting") or {}
    slices = slices if isinstance(slices, list) else []
    return {
        "groups": groups_raw.get("Live2d") or {},
        "infos": infos,
        "slices": slices,
        "entries": build_entries(infos, slices),
        "delete_ids": [str(x) for x in (delete_raw.get("Live2d") or [])],
        "setting_ids": [str(x) for x in (setting_raw.get("ClassifyLive2dGroupIds") or [])],
    }


def build_entries(infos: list[dict], slices: list[dict]) -> dict[str, dict]:
    """按设备切片顺序累加偏移，得到 bundle → {slice, off, size, md5}。"""
    entries: dict[str, dict] = {}
    gi = 0
    for slice_info in slices:
        filename = str(slice_info.get("Filename") or "")
        offset = 0
        for _ in range(int(slice_info.get("FileCount") or 0)):
            if gi >= len(infos):
                break
            info = infos[gi]
            gi += 1
            size = int(info.get("Size") or 0)
            entries[str(info.get("Name"))] = {
                "slice": filename,
                "off": offset + 512,
                "size": size,
                "md5": info.get("Hash"),
            }
            offset += 512 + ((size + 511) // 512) * 512
    return entries


def group_bundles(catalog: dict, group_id: str) -> list[dict]:
    node = catalog["groups"].get(str(group_id)) or {}
    return [info for info in node.get("Infos") or [] if isinstance(info, dict)]


# ---------------------------------------------------------------- 目标装束

def _hero_maps() -> tuple[dict[str, dict], dict[str, str], dict[str, str]]:
    """返回 (IDNum→hero, internal→IDNum, painting→装束名)。"""
    hero_by_id: dict[str, dict] = {}
    path = MD / "hero_profile_cn.json"
    if path.is_file():
        for idnum, hero in json.loads(path.read_text(encoding="utf-8")).items():
            if isinstance(hero, dict):
                hero_by_id[str(idnum)] = hero

    internal_by_idnum = {
        idnum: str(hero.get("Internal Name") or "").lower()
        for idnum, hero in hero_by_id.items()
    }
    names_by_painting: dict[str, str] = {}
    skins_path = MD / "skins.json"
    if skins_path.is_file():
        for skin in json.loads(skins_path.read_text(encoding="utf-8")).get("skins", []):
            painting = str(skin.get("painting") or "")
            if painting:
                names_by_painting[painting] = str(skin.get("name") or "")
    return hero_by_id, internal_by_idnum, names_by_painting


def load_group_map() -> dict[str, dict]:
    """读取 classify_group_map.json（含已认名的新角色组）。"""
    path = MD / "classify_group_map.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def painting_index(group_map: dict[str, dict]) -> dict[str, str]:
    """映射表 → {painting: group_id}，优先于 hero_profile 推导。"""
    output: dict[str, str] = {}
    for gid, entry in group_map.items():
        if not isinstance(entry, dict):
            continue
        internal = str(entry.get("internal") or "").lower()
        try:
            seq = int(entry.get("seq") or 0)
        except (TypeError, ValueError):
            continue
        if internal and seq:
            output[f"{internal}_{seq}"] = str(gid)
    return output


def idnum_index(group_map: dict[str, dict]) -> dict[str, str]:
    """映射表 → {internal: IDNum}，用于给同一角色的新装束推导组 ID。"""
    output: dict[str, str] = {}
    for gid, entry in group_map.items():
        if not isinstance(entry, dict):
            continue
        internal = str(entry.get("internal") or "").lower()
        if internal and len(str(gid)) == 8:
            output.setdefault(internal, str(gid)[:6])
    return output


def expected_group_id(
    skin: dict,
    internal_by_idnum: dict[str, str],
    idnum_by_internal: dict[str, str] | None = None,
) -> str:
    """painting（如 hameier_5）→ 组 ID（IDNum 6 位 + 序号 2 位）。"""
    painting = str(skin.get("painting") or "")
    match = re.match(r"^([a-z0-9]+?)_(\d+)$", painting, re.I)
    if not match:
        return ""
    internal, seq = match.group(1).lower(), int(match.group(2))
    idnum = next(
        (key for key, value in internal_by_idnum.items() if value == internal), ""
    )
    if not idnum:
        # hero_profile 快照可能缺新角色，用映射表里同角色已有组反推 IDNum
        idnum = (idnum_by_internal or {}).get(internal, "")
    return f"{idnum}{seq:02d}" if idnum else ""


def local_model_dir(skin: dict) -> Path | None:
    """按 painting 大小写不敏感地找已存在且完整的模型目录。"""
    painting = str(skin.get("painting") or "").lower()
    if not painting:
        return None
    live2d_root = EXTRACTED / "live2d"
    if not live2d_root.is_dir():
        return None
    for cand in live2d_root.iterdir():
        if cand.is_dir() and cand.name.lower() == painting and (cand / "model3.json").is_file():
            return cand
    return None


def select_targets(
    skins: list[dict],
    catalog: dict,
    internal_by_idnum: dict[str, str],
    *,
    group_by_painting: dict[str, str] | None = None,
    idnum_by_internal: dict[str, str] | None = None,
    only_missing: bool = True,
    paintings: list[str] | None = None,
    limit: int = 0,
) -> tuple[list[dict], list[dict]]:
    """返回 (可导出, 不可用)。按 release_date 倒序（新的优先）。"""
    wanted = {p.lower() for p in paintings or []}
    known = group_by_painting or {}
    rows: list[dict] = []
    for skin in skins:
        if str(skin.get("type")) != "live2d":
            continue
        painting = str(skin.get("painting") or "")
        if not painting:
            continue
        if wanted and painting.lower() not in wanted:
            continue
        if only_missing and local_model_dir(skin) is not None:
            continue
        gid = known.get(painting.lower()) or expected_group_id(
            skin, internal_by_idnum, idnum_by_internal
        )
        bundles = group_bundles(catalog, gid) if gid else []
        entries = catalog.get("entries") or {}
        slice_ready = not entries or all(
            str(info.get("Name")) in entries for info in bundles
        )
        rows.append({
            "skin": skin,
            "painting": painting,
            "group_id": gid,
            "bundles": bundles,
            "slice_ready": bool(bundles) and slice_ready,
            "release_date": str(skin.get("release_date") or ""),
        })
    rows.sort(key=lambda item: item["release_date"], reverse=True)

    ready = [row for row in rows if row["group_id"] and row.get("slice_ready")]
    blocked = [row for row in rows if not (row["group_id"] and row.get("slice_ready"))]
    if limit:
        ready = ready[:limit]
    return ready, blocked


def describe_blocked(row: dict, catalog: dict, delete_ids: list[str]) -> str:
    skin = row["skin"]
    name = f"{skin.get('character') or ''}·{skin.get('name') or ''}".strip("·")
    if not row["group_id"]:
        return f"{name}（{row['painting']}）：无法推导组 ID（hero_profile 缺少该角色）"
    if row["group_id"] in delete_ids:
        return f"{name}（{row['group_id']}）：该组已在设备上删除"
    if row.get("bundles") and not row.get("slice_ready"):
        return f"{name}（{row['group_id']}）：组内切片未下发，需先在游戏内下载该装束"
    if row["group_id"] in catalog["setting_ids"]:
        return f"{name}（{row['group_id']}）：组在待下载列表，客户端尚未下完"
    return f"{name}（{row['group_id']}）：设备清单无该组，需先在游戏内下载/拥有该装束"


# ---------------------------------------------------------------- adb 交互

def adb_devices(adb: Path) -> list[str]:
    out = subprocess.run([str(adb), "devices"], capture_output=True, text=True, timeout=30)
    serials = []
    for line in out.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def ensure_device(adb: Path, serial: str = "") -> str:
    serials = adb_devices(adb)
    if serial and serial in serials:
        return serial
    if len(serials) == 1:
        return serials[0]
    if serials:
        raise RuntimeError(f"检测到多台设备 {serials}，请用 --serial 指定")
    for endpoint in CONNECT_CANDIDATES:
        subprocess.run([str(adb), "connect", endpoint], capture_output=True, text=True)
        serials = adb_devices(adb)
        if serials:
            print(f"[adb] 已连接 {endpoint}")
            return serials[0]
    raise RuntimeError(
        "没有可用的 adb 设备：请先启动 MuMu 并进入游戏，或用 --device-files 指向已拷出的目录"
    )


def adb_pull(adb: Path, serial: str, remote: str, local: Path) -> bool:
    local.parent.mkdir(parents=True, exist_ok=True)
    out = subprocess.run(
        [str(adb), "-s", serial, "pull", remote, str(local)],
        capture_output=True, text=True,
    )
    return out.returncode == 0 and local.is_file()


def device_files_root(adb: Path, serial: str, package: str) -> str:
    for root in (
        f"/sdcard/Android/data/{package}/files",
        f"/storage/emulated/0/Android/data/{package}/files",
    ):
        probe = subprocess.run(
            [str(adb), "-s", serial, "shell", "test", "-d", root],
            capture_output=True, text=True,
        )
        if probe.returncode == 0:
            return root
    raise RuntimeError(f"设备上找不到游戏文件目录：/sdcard/Android/data/{package}/files")


def pull_manifests(adb: Path, serial: str, files_root: str, dest: Path) -> Path:
    """拉取清单文件到本地 dest（保留 fs/ 与 fsclassify/ 相对结构）。"""
    names = (
        ("fs", "res_base_classify.json"),
        ("fs", "res_classify.json"),
        ("fsclassify", "res_install.json"),
        ("fsclassify", "res_base_classify_delete.json"),
        ("fsclassify", "res_classify_download_setting.json"),
    )
    pulled = 0
    for folder, name in names:
        if adb_pull(adb, serial, f"{files_root}/{folder}/{name}", dest / folder / name):
            pulled += 1
    if not (dest / "fs" / "res_base_classify.json").is_file():
        # 兼容部分版本把清单放 fsclassify
        alt = dest / "fsclassify" / "res_base_classify.json"
        if alt.is_file():
            shutil.copy2(alt, dest / "fs" / "res_base_classify.json")
    if pulled == 0:
        raise RuntimeError("清单拉取失败：请确认设备已进入游戏并完成资源同步")
    print(f"[清单] 已拉取 {pulled} 个文件 -> {dest}")
    return dest


# ---------------------------------------------------------------- 导出与索引

def pull_group_bundles(
    adb: Path, serial: str, files_root: str, bundles: list[dict], staging: Path
) -> tuple[list[Path], list[str]]:
    """只拉一个组的 bundle 文件；返回 (已拉取, 缺失)。"""
    pulled: list[Path] = []
    missing: list[str] = []
    for info in bundles:
        name = str(info.get("Name") or "")
        if not name.startswith("/assets/"):
            missing.append(name or "<空>")
            continue
        rel = name.lstrip("/")
        local = staging / "fsclassify" / rel
        if local.is_file() and local.stat().st_size == int(info.get("Size") or 0):
            pulled.append(local)
            continue
        if adb_pull(adb, serial, f"{files_root}/fsclassify/{rel}", local):
            pulled.append(local)
        else:
            missing.append(name)
    return pulled, missing


def export_group(staging: Path, painting: str, out_root: Path | None = None) -> dict:
    from tools.assemble_classify import assemble_from_group

    return assemble_from_group(staging, out_root or (EXTRACTED / "live2d"), painting)


def refresh_indexes(
    catalog: dict, *, index_path: Path | None = None, map_path: Path | None = None
) -> None:
    """用设备清单重建 classify_index.json 并合并 classify_group_map.json。"""
    index_path = Path(index_path or (MD / "classify_index.json"))
    map_path = Path(map_path or (MD / "classify_group_map.json"))
    infos = catalog["infos"]
    slices = catalog["slices"]
    if not infos or not slices:
        print("[索引] 清单缺少 res_classify/res_install，跳过索引重建")
        return

    entries = build_entries(infos, slices)

    groups: dict[str, dict] = {}
    for gid, node in catalog["groups"].items():
        infos_g = [i for i in (node or {}).get("Infos") or [] if isinstance(i, dict)]
        groups[str(gid)] = {
            "bundles": [str(i.get("Name")) for i in infos_g],
            "total": sum(int(i.get("Size") or 0) for i in infos_g),
            "complete": all(str(i.get("Name")) in entries for i in infos_g),
        }

    payload = {
        "major": "2.5",
        "slice_version": int((slices[0] or {}).get("Version") or 0),
        "slices": {str(s.get("Filename")): int(s.get("FileSize") or 0) for s in slices},
        "entries": entries,
        "groups": groups,
    }
    index_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"[索引] classify_index.json：{len(entries)} 条条目 / {len(groups)} 组")

    # 合并组映射：只为新组补名，不覆盖已有条目
    existing = {}
    if map_path.is_file():
        existing = json.loads(map_path.read_text(encoding="utf-8"))
    hero_by_id, _, names_by_painting = _hero_maps()
    added = 0
    for gid in groups:
        if gid in existing:
            continue
        idnum = gid[:6]
        try:
            seq = int(gid[6:])
        except ValueError:
            continue
        hero = hero_by_id.get(idnum) or {}
        internal = str(hero.get("Internal Name") or "").lower()
        painting = f"{internal}_{seq}" if internal else ""
        entry = {
            "group_id": gid,
            "id_num": idnum,
            "seq": f"{seq:02d}",
            "internal": internal,
            "character": str(hero.get("姓名") or ""),
            "skin_name": names_by_painting.get(painting, ""),
            "map_source": "device_catalog",
            "name_source": "skins" if names_by_painting.get(painting) else "",
            "downloadable": bool(groups[gid]["complete"]),
            "total_mb": round(groups[gid]["total"] / 1048576, 1),
            "review": not internal,
        }
        existing[gid] = entry
        added += 1
    if added:
        map_path.write_text(json.dumps(existing, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[索引] classify_group_map.json：新增 {added} 组")


def rebuild_local_library() -> None:
    for tool in ("build_local_index.py", "link_skins.py"):
        path = ROOT / "tools" / tool
        result = subprocess.run(
            [sys.executable, "-I", str(path)], capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"[索引] {tool} 失败：{(result.stderr or result.stdout)[-300:]}")
            return
    print("[索引] 本地索引与图鉴挂接已重建")


# ---------------------------------------------------------------- 主流程

# ------------------------------------------------------------ 预览抓取模式

def adb_find_files(adb: Path, serial: str, roots: list[str]) -> set[str]:
    """列设备上若干目录下的全部文件路径。"""
    command = "find " + " ".join(roots) + " -type f 2>/dev/null"
    result = subprocess.run(
        [str(adb), "-s", serial, "shell", command],
        capture_output=True, text=True, timeout=180,
    )
    return {
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith("/")
    }


def wait_for_new_files(
    adb: Path, serial: str, roots: list[str], timeout: int, interval: float = 3.0
) -> list[str]:
    """轮询文件系统，等用户在游戏里预览装束时产生的下载。"""
    before = adb_find_files(adb, serial, roots)
    print(
        f"[抓取] 当前 {len(before)} 个文件。\n"
        "       现在请在游戏里打开该装束的预览（购买界面/图鉴均可），"
        f"最多等待 {timeout}s…"
    )
    import time

    deadline = time.time() + timeout
    fresh: set[str] = set()
    stable = 0
    while time.time() < deadline:
        time.sleep(interval)
        now = adb_find_files(adb, serial, roots)
        changed = now - before
        if not changed:
            continue
        if changed == fresh:
            stable += 1
            if stable >= 2:
                break
        else:
            fresh = changed
            stable = 0
            print(f"[抓取] 发现 {len(fresh)} 个新文件…")
    return sorted(fresh)


def probe_bundle(raw: bytes) -> tuple[str, str, bool]:
    """返回 (AssetBundle 名, moc 模型名, 是否含 MOC3)。"""
    import io

    import UnityPy

    ab_name = ""
    model_name = ""
    i_moc = raw.find(b"MOC3")
    i_v8 = raw.rfind(b"UnityFS")
    has_moc = 0 <= i_moc < (i_v8 if i_v8 > 0 else len(raw))
    body = raw[i_v8:] if i_v8 > 0 else raw
    try:
        env = UnityPy.load(io.BytesIO(body))
        for obj in env.objects:
            if obj.type.name == "AssetBundle":
                ab_name = obj.read().m_Name or ""
            elif obj.type.name == "MonoBehaviour" and not model_name:
                try:
                    name = obj.read().m_Name or ""
                except Exception:  # noqa: BLE001
                    continue
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*_\d+", name):
                    model_name = name
    except Exception:  # noqa: BLE001
        pass
    return ab_name, model_name, has_moc


def normalize_family(prefix: str) -> str:
    """贴图 bundle 会重复模型名（characters_ceto_4_ceto_4 → characters_ceto_4）。"""
    match = re.match(r"^(.*)_([A-Za-z0-9]+_\d+)$", prefix)
    if match and match.group(1).endswith("_" + match.group(2)):
        return match.group(1)
    return prefix


def painting_from_family(family: str) -> str:
    """家族前缀 → painting（characters_ceto_4 / cg_chain_2 → ceto_4 / chain_2）。"""
    for prefix in ("characters_", "cg_"):
        if family.startswith(prefix):
            return family[len(prefix):]
    return family


def group_captured(entries: list[dict]) -> dict[str, list[dict]]:
    """按家族分组；moc 容器没有 AssetBundle 名时用 MonoBehaviour 名兜底。"""
    from tools.assemble_live2d import _family_member

    groups: dict[str, list[dict]] = {}
    for entry in entries:
        family = ""
        if entry.get("ab"):
            member = _family_member(str(entry["ab"]))
            if member:
                family = normalize_family(member[0])
        if not family and entry.get("model"):
            family = "characters_" + str(entry["model"]).lower()
        if not family:
            continue
        groups.setdefault(family, []).append(entry)
    return groups


def run_watch(args, adb: Path, serial: str, remote_root: str) -> int:
    roots = [
        f"{remote_root}/fsclassify",
        f"{remote_root}/fstier1",
        f"{remote_root}/fs",
        f"/sdcard/Android/data/{args.package}/cache",
    ]
    new_files = wait_for_new_files(adb, serial, roots, args.watch_timeout)
    if not new_files:
        print(
            "[抓取] 没有发现新文件。可能预览是流式加载、没有落盘；"
            "可尝试停留更久，或改用 --device-files 扫描游戏目录。"
        )
        return 1

    raw_dir = WORK / "watch" / "_raw"
    shutil.rmtree(raw_dir, ignore_errors=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    for remote in new_files:
        local = raw_dir / Path(remote).name
        if not adb_pull(adb, serial, remote, local):
            continue
        try:
            raw = local.read_bytes()
        except OSError:
            continue
        ab_name, model_name, has_moc = probe_bundle(raw)
        entries.append({
            "path": local,
            "ab": ab_name,
            "model": model_name,
            "has_moc": has_moc,
        })

    groups = group_captured(entries)
    print(f"[抓取] 识别出 {len(groups)} 个 Live2D 家族：")
    exported: list[tuple[str, bool, str]] = []
    for family, members in sorted(groups.items()):
        painting = painting_from_family(family)
        has_moc = any(item["has_moc"] for item in members)
        print(f"  - {family}（{len(members)} 个文件，moc={'是' if has_moc else '否'}）")
        if not has_moc or not re.match(r"^[a-z0-9]+?_\d+$", painting, re.I):
            continue
        staging = WORK / "watch" / family
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        for item in members:
            shutil.copy2(item["path"], staging / item["path"].name)
        try:
            result = export_group(staging, painting)
            exported.append((painting, True, f"贴图 {result['textures']} 张 / 动作 {result['motions']} 条"))
        except Exception as exc:  # noqa: BLE001
            exported.append((painting, False, str(exc)[:160]))

    print("\n===== 抓取导出结果 =====")
    ok = 0
    for painting, success, detail in exported:
        print(f"  {'OK ' if success else 'FAIL'} {painting}: {detail}")
        ok += 1 if success else 0
    print(f"成功 {ok} / {len(exported)}")
    if ok and not args.no_index:
        rebuild_local_library()
    return 0 if ok else 1



# ------------------------------------------------------------ 设备 chunk 扫描

def iter_chunk_bodies(raw: bytes):
    """兼容 PTN chunk 的三种块布局：
    1) [50B 前缀][明文 UnityFS v8]（当前线上，块间 64 字节对齐）
    2) 纯 UnityFS v8（无前缀，同样 64 字节对齐）
    3) [50B 前缀][XOR 的 UnityFS]（旧版）
    """
    import struct

    def size_at(pos: int) -> int:
        try:
            return struct.unpack_from(">Q", raw, pos + 30)[0]
        except struct.error:
            return 0

    off = 0
    n = len(raw)
    while off + 58 <= n:
        if raw[off:off + 7] == b"UnityFS":
            body_off = off + 50
            if raw[body_off:body_off + 7] == b"UnityFS":
                fs = size_at(body_off)
                if 128 <= fs <= n - body_off:
                    yield raw[body_off:body_off + fs]
                    off = ((body_off + fs + 63) // 64) * 64
                    continue
            fs = size_at(off)
            if 128 <= fs <= n - off:
                yield raw[off:off + fs]
                off = ((off + fs + 63) // 64) * 64
                continue
            break
        from tools.split_ptn_chunk import read_block

        try:
            block = read_block(raw, off)
        except ValueError:
            block = None
        if not block:
            break
        yield block[0]
        off = block[1]


def run_scan_chunks(args, adb: Path, serial: str, remote_root: str) -> int:
    """扫描设备 fschunk 下的全部 chunk，按内容找出目标 Live2D 家族并装配。"""
    wanted = {p.lower() for p in args.painting}
    if not wanted:
        raise RuntimeError("扫描设备 chunk 必须用 --painting 指定目标（避免全量导出）")

    listing = subprocess.run(
        [str(adb), "-s", serial, "shell", f"ls {remote_root}/fschunk/*.bin"],
        capture_output=True, text=True, timeout=120,
    )
    chunks = [
        line.strip() for line in listing.stdout.splitlines()
        if line.strip().startswith("/") and line.strip().endswith(".bin")
    ]
    if not chunks:
        raise RuntimeError("设备 fschunk 下没有 chunk 文件")
    print(f"[chunk] 共 {len(chunks)} 个 chunk，目标 {', '.join(sorted(wanted))}")

    work = WORK / "device_chunks"
    work.mkdir(parents=True, exist_ok=True)
    staging_root = work / "staging"
    shutil.rmtree(staging_root, ignore_errors=True)
    staging_root.mkdir(parents=True, exist_ok=True)

    found: dict[str, list[Path]] = {}
    for index, remote in enumerate(chunks, 1):
        name = Path(remote).name
        local = work / name
        if not local.is_file() or local.stat().st_size == 0:
            if not adb_pull(adb, serial, remote, local):
                print(f"[{index}/{len(chunks)}] 拉取失败 {name}")
                continue
        raw = local.read_bytes()
        blocks = 0
        hits = 0
        for body in iter_chunk_bodies(raw):
            blocks += 1
            ab_name, model_name, has_moc = probe_bundle(body)
            family = ""
            if ab_name:
                from tools.assemble_live2d import _family_member

                member = _family_member(ab_name)
                if member:
                    family = normalize_family(member[0])
            if not family and model_name:
                family = "characters_" + model_name.lower()
            if not family:
                continue
            painting = painting_from_family(family).lower()
            if painting not in wanted:
                continue
            target_dir = staging_root / family
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{name}_{blocks:04d}.bundle"
            target.write_bytes(body)
            found.setdefault(family, []).append(target)
            hits += 1
        print(f"[{index}/{len(chunks)}] {name}: {blocks} 块，命中 {hits}", flush=True)
        if not args.keep_staging:
            local.unlink(missing_ok=True)

    if not found:
        print("[chunk] 没有找到目标家族")
        return 1

    results: list[tuple[str, bool, str]] = []
    for family, files in sorted(found.items()):
        painting = painting_from_family(family)
        existing = local_model_dir({"painting": painting})
        if existing is not None and not args.force:
            results.append((painting, True, f"本地已存在，跳过（--force 覆盖）：{existing.name}"))
            continue
        try:
            print(f"\n[装配] {painting}（{len(files)} 个块）…")
            result = export_group(staging_root / family, painting)
            results.append((painting, True, f"贴图 {result['textures']} 张 / 动作 {result['motions']} 条"))
        except Exception as exc:  # noqa: BLE001
            results.append((painting, False, str(exc)[:200]))

    print("\n===== chunk 扫描结果 =====")
    ok = 0
    for painting, success, detail in results:
        print(f"  {'OK ' if success else 'FAIL'} {painting}: {detail}")
        ok += 1 if success else 0
    if ok and not args.no_index:
        rebuild_local_library()
    return 0 if ok else 1


def run_full_export(args, adb: Path, serial: str, remote_root: str) -> int:
    """全量导出：扫设备 fschunk 全部 chunk + fsclassify 全部 bundle，按家族合并装配。"""
    from tools.assemble_live2d import _family_member

    out_root = Path(args.out) if args.out else (EXTRACTED / "game_full")
    out_root.mkdir(parents=True, exist_ok=True)
    work = WORK / "full"
    staging = work / "staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    moc_families: set[str] = set()
    family_count = 0
    block_count = 0

    def absorb(body: bytes, tag: str) -> None:
        nonlocal family_count, block_count
        ab_name, model_name, has_moc = probe_bundle(body)
        family = ""
        if ab_name:
            member = _family_member(ab_name)
            if member:
                family = normalize_family(member[0])
        if not family and model_name:
            family = "characters_" + model_name.lower()
        if not family:
            return
        target = staging / family
        if not target.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            family_count += 1
        (target / (tag + ".bundle")).write_bytes(body)
        block_count += 1
        if has_moc:
            moc_families.add(family)

    print("[全量] 输出目录: " + str(out_root))

    listing = subprocess.run(
        [str(adb), "-s", serial, "shell", "ls " + remote_root + "/fschunk/*.bin"],
        capture_output=True, text=True, timeout=120,
    )
    chunks = [line.strip() for line in listing.stdout.splitlines()
              if line.strip().startswith("/") and line.strip().endswith(".bin")]
    print("[全量] chunk 数量: " + str(len(chunks)))
    chunk_dir = work / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    for index, remote in enumerate(chunks, 1):
        name = Path(remote).name
        local = chunk_dir / name
        if not adb_pull(adb, serial, remote, local):
            print("[chunk] 拉取失败: " + name)
            continue
        raw = local.read_bytes()
        blocks = 0
        for body in iter_chunk_bodies(raw):
            blocks += 1
            absorb(body, "c{0:03d}_{1:05d}".format(index, blocks))
        local.unlink(missing_ok=True)
        print("[chunk {0}/{1}] {2}: {3} 块，家族 {4}".format(
            index, len(chunks), name, blocks, family_count), flush=True)

    listing = subprocess.run(
        [str(adb), "-s", serial, "shell",
         "find " + remote_root + "/fsclassify/assets -name '*.bundle'"],
        capture_output=True, text=True, timeout=180,
    )
    bundles = [line.strip() for line in listing.stdout.splitlines()
               if line.strip().startswith("/")]
    print("[全量] fsclassify bundle 数量: " + str(len(bundles)))
    tmp = work / "one.bundle"
    for index, remote in enumerate(bundles, 1):
        if not adb_pull(adb, serial, remote, tmp):
            continue
        try:
            absorb(tmp.read_bytes(), "f{0:05d}".format(index))
        finally:
            tmp.unlink(missing_ok=True)
        if index % 200 == 0 or index == len(bundles):
            print("[bundle {0}/{1}] 家族 {2}".format(index, len(bundles), family_count), flush=True)

    print("[全量] 扫描完成: {0} 个 Live2D bundle, {1} 个家族, 含 moc {2} 个".format(
        block_count, family_count, len(moc_families)))

    results: list[tuple[str, bool, str]] = []
    for family in sorted(moc_families):
        painting = painting_from_family(family)
        if not re.match(r"^[a-z0-9]+?_\d+$", painting, re.I):
            results.append((painting, False, "家族名不符合 <internal>_<seq>"))
            continue
        if (out_root / painting / "model3.json").is_file() and not args.force:
            results.append((painting, True, "已存在，跳过"))
            continue
        try:
            print("[装配] " + painting, flush=True)
            result = export_group(staging / family, painting, out_root)
            results.append((painting, True, "贴图 {0} 张 / 动作 {1} 条".format(
                result["textures"], result["motions"])))
        except Exception as exc:  # noqa: BLE001
            results.append((painting, False, str(exc)[:160]))

    print("[全量] 装配结果:")
    ok = 0
    for painting, success, detail in results:
        print("  {0} {1}: {2}".format("OK " if success else "FAIL", painting, detail))
        ok += 1 if success else 0
    print("[全量] 成功 {0} / {1}，输出 {2}".format(ok, len(results), out_root))
    if not args.keep_staging:
        shutil.rmtree(staging, ignore_errors=True)
    return 0 if ok else 1


def run_scan(args) -> int:
    """扫描一个已导出的游戏目录/资产 dump，按内容识别并导出全部 Live2D 家族。"""
    root = Path(args.scan_dir)
    if not root.is_dir():
        raise RuntimeError(f"目录不存在：{root}")
    bundles = sorted(p for p in root.rglob("*.bundle") if p.is_file())
    if not bundles:
        raise RuntimeError(f"目录里没有 .bundle 文件：{root}")
    print(f"[扫描] {len(bundles)} 个 bundle：{root}")

    entries: list[dict] = []
    for path in bundles:
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        ab_name, model_name, has_moc = probe_bundle(raw)
        entries.append({
            "path": path, "ab": ab_name, "model": model_name, "has_moc": has_moc,
        })

    groups = group_captured(entries)
    live = {family: members for family, members in groups.items()
            if any(item["has_moc"] for item in members)}
    wanted = {p.lower() for p in args.painting}
    if wanted:
        live = {
            family: members for family, members in live.items()
            if painting_from_family(family).lower() in wanted
        }
    print(f"[扫描] 识别出 {len(live)} 个含 moc 的 Live2D 家族")
    if args.dry_run:
        for family, members in sorted(live.items()):
            print(f"  - {painting_from_family(family)}（{len(members)} 个文件）")
        return 0

    results: list[tuple[str, bool, str]] = []
    for family, members in sorted(live.items()):
        painting = painting_from_family(family)
        if not re.match(r"^[a-z0-9]+?_\d+$", painting, re.I):
            results.append((painting, False, "家族名不符合 <internal>_<seq>"))
            continue
        existing = local_model_dir({"painting": painting})
        if existing is not None and not args.force:
            results.append((painting, True, f"本地已存在，跳过（--force 可覆盖）：{existing.name}"))
            continue
        staging = WORK / "scan" / family
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        for item in members:
            shutil.copy2(item["path"], staging / item["path"].name)
        try:
            print(f"\n[装配] {painting} …")
            result = export_group(staging, painting)
            results.append((painting, True, f"贴图 {result['textures']} 张 / 动作 {result['motions']} 条"))
        except Exception as exc:  # noqa: BLE001
            results.append((painting, False, str(exc)[:200]))
        finally:
            if not args.keep_staging:
                shutil.rmtree(staging, ignore_errors=True)

    print("\n===== 扫描导出结果 =====")
    ok = 0
    for painting, success, detail in results:
        print(f"  {'OK ' if success else 'FAIL'} {painting}: {detail}")
        ok += 1 if success else 0
    print(f"成功 {ok} / {len(results)}")
    if ok and not args.no_index:
        rebuild_local_library()
    return 0 if ok else 1


def run(args) -> int:
    if args.device_files:
        files_root = Path(args.device_files)
        pull_dir = files_root
    else:
        adb = Path(args.adb)
        if not adb.is_file():
            raise RuntimeError(f"找不到 adb：{adb}（可用 --adb 指定）")
        serial = ensure_device(adb, args.serial)
        print(f"[adb] 设备 {serial}")
        remote_root = device_files_root(adb, serial, args.package)
        if args.watch:
            return run_watch(args, adb, serial, remote_root)
        if args.full_export:
            return run_full_export(args, adb, serial, remote_root)
        if args.scan_chunks:
            return run_scan_chunks(args, adb, serial, remote_root)
        pull_dir = WORK / "manifests"
        pull_manifests(adb, serial, remote_root, pull_dir)

    catalog = load_catalog(pull_dir)
    print(
        f"[清单] Live2d 组 {len(catalog['groups'])} 个，条目 {len(catalog['infos'])} 条，"
        f"切片 {len(catalog['slices'])} 个"
    )

    skins = json.loads((MD / "skins.json").read_text(encoding="utf-8")).get("skins", [])
    _, internal_by_idnum, _ = _hero_maps()
    group_map = load_group_map()
    ready, blocked = select_targets(
        skins,
        catalog,
        internal_by_idnum,
        group_by_painting=painting_index(group_map),
        idnum_by_internal=idnum_index(group_map),
        only_missing=not args.all,
        paintings=args.painting,
        limit=args.limit,
    )

    print(f"\n可导出 {len(ready)} 个装束：")
    for row in ready:
        print(f"  - {row['skin'].get('character')}·{row['skin'].get('name')} "
              f"({row['painting']}, 组 {row['group_id']}, {len(row['bundles'])} bundle)")
    if blocked:
        print(f"\n暂不可用 {len(blocked)} 个：")
        for row in blocked:
            print("  - " + describe_blocked(row, catalog, catalog["delete_ids"]))
    if args.dry_run or not ready:
        return 0

    # adb 模式：为每组只拉需要的 bundle
    serial = "" if args.device_files else serial
    adb = Path(args.adb)
    results: list[tuple[str, bool, str]] = []
    for row in ready:
        painting = row["painting"]
        staging = WORK / "staging" / painting
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        if args.device_files:
            # 本地目录模式：直接从 dump 里拷目标 bundle
            pulled, missing = [], []
            for info in row["bundles"]:
                name = str(info.get("Name") or "").lstrip("/")
                src = Path(args.device_files) / "fsclassify" / name
                dst = staging / "fsclassify" / name
                if src.is_file():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    pulled.append(dst)
                else:
                    missing.append(name)
        else:
            pulled, missing = pull_group_bundles(
                adb, serial, remote_root, row["bundles"], staging
            )
        if missing:
            results.append((painting, False, f"缺 {len(missing)} 个文件（未下载？）"))
            continue
        try:
            print(f"\n[装配] {painting} …")
            result = export_group(staging, painting)
            results.append((painting, True, f"贴图 {result['textures']} 张 / 动作 {result['motions']} 条"))
        except Exception as exc:  # noqa: BLE001
            results.append((painting, False, str(exc)[:160]))
        finally:
            if not args.keep_staging:
                shutil.rmtree(staging, ignore_errors=True)

    print("\n===== 导出结果 =====")
    ok = 0
    for painting, success, detail in results:
        print(f"  {'OK ' if success else 'FAIL'} {painting}: {detail}")
        ok += 1 if success else 0
    print(f"成功 {ok} / {len(results)}")

    if ok and not args.no_index:
        rebuild_local_library()
    if args.refresh_index:
        refresh_indexes(catalog)
    return 0 if ok or not results else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="从模拟器一键导出最新/缺失的 Live2D 装束")
    ap.add_argument("--adb", default=str(DEFAULT_ADB), help="adb 可执行文件路径")
    ap.add_argument("--serial", default="", help="adb 设备序列号（多设备时必填）")
    ap.add_argument("--package", default=DEFAULT_PACKAGE, help="游戏包名")
    ap.add_argument("--device-files", default="", help="已拷到本地的 files 目录（跳过 adb）")
    ap.add_argument("--full-export", action="store_true",
                    help="全量导出：扫 fschunk 全部 chunk + fsclassify 全部 bundle")
    ap.add_argument("--out", default="", help="全量导出输出目录（默认 resources/extracted/game_full）")
    ap.add_argument("--scan-chunks", action="store_true",
                    help="扫描设备 fschunk 全部 chunk，按内容找 --painting 指定的家族")
    ap.add_argument("--keep-chunks", action="store_true",
                    help="扫描 chunk 时保留拉下来的 chunk 文件")
    ap.add_argument("--scan-dir", default="",
                    help="扫描已导出的游戏目录/资产 dump，按内容识别并导出全部 Live2D")
    ap.add_argument("--force", action="store_true",
                    help="扫描模式下覆盖本地已存在的模型")
    ap.add_argument("--watch", action="store_true",
                    help="抓取模式：等你游戏内预览时下载的资源，再装配导出")
    ap.add_argument("--watch-timeout", type=int, default=240,
                    help="抓取模式最长等待秒数（默认 240）")
    ap.add_argument("--dry-run", action="store_true", help="只输出可导出/不可用清单")
    ap.add_argument("--all", action="store_true", help="导出全部装束（默认只导本地缺失的）")
    ap.add_argument("--painting", nargs="*", default=[], help="只处理指定 painting")
    ap.add_argument("--limit", type=int, default=0, help="最多导出 N 个（按上线日期倒序）")
    ap.add_argument("--refresh-index", action="store_true", help="同时重建 classify 索引与组映射")
    ap.add_argument("--no-index", action="store_true", help="导出后不重建本地索引")
    ap.add_argument("--keep-staging", action="store_true", help="保留临时 bundle 目录")
    args = ap.parse_args()

    if args.scan_dir:
        try:
            return run_scan(args)
        except Exception as exc:  # noqa: BLE001
            print(f"[失败] {exc}")
            return 2
    if args.watch and args.device_files:
        print("[失败] --watch 需要 adb 连接，不能与 --device-files 同用")
        return 2
    try:
        return run(args)
    except Exception as exc:  # noqa: BLE001
        print(f"[失败] {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
