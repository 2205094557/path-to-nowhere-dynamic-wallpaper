# -*- coding: utf-8 -*-
"""无期迷途官方热更 CDN 来源插件与按装束资源定位。"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.request
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core.registry import SourcePlugin

CDN_HOSTS = (
    "https://xonecn-hotupdatecdn.shziyi.com",
    "https://xonecn-hotupdatecdn-backup.shziyi.com",
)
PLATFORM = "android"
REGION = "release-cn"
MAJOR = "2.4"
# 分类资源（Live2D 等可选内容）走独立分支与 slice 版本，实测见 docs/CDN_RESEARCH_LOG_3.md
CLASSIFY_MAJOR = "2.5"

_HEADERS = {"User-Agent": "UnityPlayer/2019.4.40f1"}
_TIMEOUT = 60
_ATTEMPTS = 3
_CHUNK_RE = re.compile(r"^res_[0-9a-f]{32}\.bin$", re.I)
_SLICE_RE = re.compile(r"^res_[0-9a-f]{32}\.slice$", re.I)


@dataclass
class ChunkInfo:
    name: str
    url: str
    size: int = 0

    @property
    def key(self) -> str:
        return self.name


@dataclass(frozen=True)
class SkinTarget:
    """library_state 中解析出的单装束下载目标。"""

    kind: str
    painting: str
    chunk: str
    asset_name: str = ""
    model_name: str = ""


def load_library_state(path: str | Path) -> dict:
    """读取并校验按装束下载所需的离线索引。"""
    p = Path(path)
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise RuntimeError(f"资源定位索引不存在: {p}") from e
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"资源定位索引读取失败: {p}: {e}") from e
    if not isinstance(state, dict):
        raise RuntimeError(f"资源定位索引格式错误: {p}")
    for key in ("static_best", "moc3_bundles"):
        if not isinstance(state.get(key), dict):
            raise RuntimeError(f"资源定位索引缺少 {key}: {p}")
    return state


def _safe_identifier(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text or text in (".", "..") or Path(text).name != text or "/" in text or "\\" in text:
        raise RuntimeError(f"装束缺少有效的{label}")
    return text


def _chunk_name(entry: object, label: str) -> str:
    if not isinstance(entry, dict):
        raise RuntimeError(f"{label} 的资源索引条目格式错误")
    chunk = str(entry.get("chunk") or entry.get("bundle") or "")
    if not _CHUNK_RE.fullmatch(chunk):
        raise RuntimeError(f"{label} 的 chunk 名无效: {chunk or '空'}")
    return chunk


def _match_key_casefold(index: dict, wanted: str) -> str | None:
    folded = wanted.casefold()
    return next((str(key) for key in index if str(key).casefold() == folded), None)


def _model_key(value: object) -> str:
    return "".join(ch.casefold() for ch in str(value or "") if ch.isalnum())


def resolve_static_target(skin: dict, state: dict) -> SkinTarget:
    painting = _safe_identifier(skin.get("painting"), "painting")
    static_best = state.get("static_best")
    if not isinstance(static_best, dict):
        raise RuntimeError("资源定位索引缺少 static_best")
    asset_name = _match_key_casefold(static_best, painting)
    if asset_name is None:
        raise RuntimeError(f"静态立绘 {painting} 不在 library_state.static_best 中")
    entry = static_best[asset_name]
    patch = str(entry.get("patch") or "") if isinstance(entry, dict) else ""
    return SkinTarget(
        kind="static_patch" if patch else "static",
        painting=painting,
        asset_name=asset_name,
        chunk=("patch:" + patch) if patch else _chunk_name(entry, f"静态立绘 {painting}"),
    )


def resolve_live2d_target(skin: dict, state: dict) -> SkinTarget:
    painting = _safe_identifier(skin.get("painting") or skin.get("name"), "painting/name")
    inventory = state.get("moc3_bundles")
    if not isinstance(inventory, dict):
        raise RuntimeError("资源定位索引缺少 moc3_bundles")

    candidates = []
    for field in ("internal_model", "model_name", "model", "moc3", "name", "painting"):
        value = str(skin.get(field) or "").strip()
        if value and value not in candidates:
            candidates.append(value)
    # 精确（忽略大小写/标点）优先，避免 Zoya_501 被相似模型误命中。
    by_norm: dict[str, list[str]] = {}
    for key in inventory:
        by_norm.setdefault(_model_key(key), []).append(str(key))
    for candidate in candidates:
        hits = by_norm.get(_model_key(candidate), [])
        if len(hits) == 1:
            model = hits[0]
            return SkinTarget(
                kind="live2d",
                painting=_safe_identifier(skin.get("painting") or model, "painting/name"),
                model_name=model,
                chunk=_chunk_name(inventory[model], f"Live2D 模型 {model}"),
            )

    # painting 常带 lihui_ 前缀；只接受最终唯一的包含关系回退。
    variants = {_model_key(v) for v in candidates if _model_key(v)}
    variants |= {v[len("lihui"):] for v in tuple(variants) if v.startswith("lihui")}
    fuzzy = {
        str(key)
        for key in inventory
        if any(v and (_model_key(key) in v or v in _model_key(key)) for v in variants)
    }
    if len(fuzzy) == 1:
        model = fuzzy.pop()
        return SkinTarget(
            kind="live2d",
            painting=_safe_identifier(skin.get("painting") or model, "painting/name"),
            model_name=model,
            chunk=_chunk_name(inventory[model], f"Live2D 模型 {model}"),
        )
    if len(fuzzy) > 1:
        raise RuntimeError(
            f"Live2D 目标不唯一（name/painting 命中 {', '.join(sorted(fuzzy))}），请提供 internal_model"
        )
    shown = ", ".join(candidates) or painting
    raise RuntimeError(
        f"Live2D 模型未在 library_state.moc3_bundles 中找到: {shown}"
        "（classify 映射也没有对应组，基础 chunk 中亦无该模型；"
        "通常是离线资源清单过旧或该装束尚未下发，"
        "需在游戏客户端更新/下载该资源后重新同步清单）"
    )


def resolve_skin_target(skin: dict, state: dict) -> SkinTarget:
    kind = str(skin.get("type") or "")
    if kind in ("static", "static_patch"):
        return resolve_static_target(skin, state)
    if kind == "live2d":
        return resolve_live2d_target(skin, state)
    raise RuntimeError(f"不支持的资源类型: {kind or '未知'}")


@dataclass(frozen=True)
class ClassifyTarget:
    """classify_group_map 中解析出的单装束下载目标（官方 classify slice 通道）。"""

    kind: str
    painting: str
    group_id: str
    internal: str
    seq: str
    total_mb: float


def load_classify_map(path: str | Path | None = None) -> dict:
    """读取分类组映射表（组 ID -> 角色/装束/可下载状态）。"""
    p = Path(path) if path else (
        Path(__file__).resolve().parents[2] / "resources" / "metadata" / "classify_group_map.json"
    )
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"分类组映射表读取失败: {p}: {e}") from e
    return data if isinstance(data, dict) else {}


def resolve_classify_target(skin: dict, group_map: dict | None = None) -> ClassifyTarget | None:
    """按 painting 在分类映射表中定位组；不覆盖/不可下载/未收录时返回 None（调用方回退）。"""
    painting = str(skin.get("painting") or "").strip().lower()
    if not painting:
        return None
    gmap = group_map if group_map is not None else load_classify_map()
    for gid, entry in gmap.items():
        if not isinstance(entry, dict) or entry.get("review"):
            continue
        internal = str(entry.get("internal") or "").lower()
        try:
            seq = int(entry.get("seq") or 0)
        except (TypeError, ValueError):
            continue
        if not internal or f"{internal}_{seq}" != painting:
            continue
        if not entry.get("downloadable"):
            return None
        return ClassifyTarget(
            kind="live2d_classify",
            painting=painting,
            group_id=str(gid),
            internal=internal,
            seq=str(seq),
            total_mb=float(entry.get("total_mb") or 0.0),
        )
    return None


class PtnCdnSource(SourcePlugin):
    kind = "sources"
    id = "ptn_cdn"
    name = "无期迷途官方CDN"

    def __init__(self) -> None:
        self._base_cache: str | None = None
        self._chunks: dict[str, ChunkInfo] = {}
        self._classify_cache: dict | None = None
        self._download_manifest_cache: dict[str, dict] | None = None

    def _base(self, cancel_event=None) -> str:
        if self._base_cache:
            return self._base_cache
        last_err: Exception | None = None
        for host in CDN_HOSTS:
            self._check_cancel(cancel_event)
            try:
                self._get(
                    self._latest_url(host, "res_version.json"),
                    cancel_event=cancel_event,
                )
                self._base_cache = host
                return host
            except RuntimeError as e:
                if str(e) == "已取消":
                    raise
                last_err = e
            except Exception as e:  # noqa: BLE001
                last_err = e
        raise RuntimeError(f"CDN 不可达: {last_err}")

    def _latest_url(self, host: str, filename: str) -> str:
        return "/".join((host, REGION, PLATFORM, "tags", "major", MAJOR, "latest", filename))

    def _chunk_url(self, host: str, name: str) -> str:
        return "/".join((host, REGION, PLATFORM, "tags", "major", MAJOR, "chunk", name))

    def _classify_data(self) -> dict:
        """读取项目级分类索引（slice/条目偏移/组→bundle）。"""
        if self._classify_cache is None:
            path = Path(__file__).resolve().parents[2] / "resources" / "metadata" / "classify_index.json"
            if not path.exists():
                raise RuntimeError("分类索引不存在：缺少 resources/metadata/classify_index.json")
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
                raise RuntimeError(f"分类索引格式错误: {path}")
            self._classify_cache = data
        return self._classify_cache

    def _classify_url(self, host: str, name: str, version: int) -> str:
        return "/".join((
            host, REGION, PLATFORM, "tags", "major", CLASSIFY_MAJOR,
            "classify", str(version), "classify", name,
        ))

    def _download_manifest(self) -> dict[str, dict]:
        """设备明文下载清单：bundle Name → {size, md5}，用于直链下载。

        清单来源：设备 fsclassify/res_base_classify_download.json（明文）。
        有了它可以把任意条目的 Name 直接拼到 classify 路径后下载，不再依赖
        slice 名与片内偏移。
        """
        if self._download_manifest_cache is not None:
            return self._download_manifest_cache
        path = Path(__file__).resolve().parents[2] / "resources" / "metadata" / "classify_download_manifest.json"
        result: dict[str, dict] = {}
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                for item in payload.get("Infos") or []:
                    name = str(item.get("Name") or "")
                    if name:
                        result[name] = {
                            "size": int(item.get("Size") or 0),
                            "md5": str(item.get("Hash") or ""),
                            "version": str(item.get("Version") or "0"),
                        }
            except (OSError, json.JSONDecodeError):
                result = {}
        self._download_manifest_cache = result
        return result

    def _direct_classify_url(self, host: str, name: str, version: int) -> str:
        """直链：.../classify/{version}/classify/assets/xx/hash.bundle"""
        return "/".join((
            host, REGION, PLATFORM, "tags", "major", CLASSIFY_MAJOR,
            "classify", str(version), "classify", name.lstrip("/"),
        ))

    def download_classify_group(
        self,
        group_id: str,
        game_dir: str | Path,
        cancel_event=None,
        emit: Callable[[str, str], None] | None = None,
    ) -> list[Path]:
        """下载一个分类组的全部 bundle。

        优先用设备明文清单拼直链（不依赖 slice/偏移）；清单里没有的条目再回退
        slice + Range。两者都校验长度与 MD5。
        """
        data = self._classify_data()
        group = (data.get("groups") or {}).get(str(group_id))
        if not isinstance(group, dict):
            raise RuntimeError(f"分类索引缺少组 {group_id}")
        entries = data.get("entries") or {}
        manifest = self._download_manifest()
        names = group.get("bundles") or []
        if not names:
            raise RuntimeError(f"组 {group_id} 没有可下载的 bundle")

        total = 0
        for n in names:
            if n in manifest:
                total += int(manifest[n]["size"])
            elif n in entries:
                total += int(entries[n]["size"])
        done = 0
        written: list[Path] = []
        host = self._base(cancel_event)
        version = int(data.get("slice_version") or 0)

        for n in names:
            self._check_cancel(cancel_event)
            h = n.rsplit("/", 1)[-1]
            dest = Path(game_dir) / "fsclassify" / "assets" / h[:2] / h
            direct = manifest.get(n)
            slice_entry = entries.get(n)
            if not direct and not slice_entry:
                raise RuntimeError(f"bundle 不在直链清单/分类索引中（组 {group_id}）: {n}")
            size = int(direct["size"]) if direct else int(slice_entry["size"])
            expected_md5 = str(direct["md5"]) if direct else str(slice_entry.get("md5") or "")
            if dest.exists() and dest.stat().st_size == size:
                done += size
                written.append(dest)
                continue

            last_err: Exception | None = None
            for attempt in range(_ATTEMPTS):
                self._check_cancel(cancel_event)
                try:
                    if direct:
                        # 直链目录由条目自身的 Version 决定（不同 bundle 可能不同版本）
                        url = self._direct_classify_url(host, n, int(direct.get("version") or version))
                        headers = dict(_HEADERS)
                    else:
                        url = self._classify_url(host, slice_entry["slice"], version)
                        headers = {**_HEADERS,
                                   "Range": f"bytes={slice_entry['off']}-{slice_entry['off'] + size - 1}"}
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=_TIMEOUT) as response:
                        status = getattr(response, "status", response.getcode())
                        if status not in (200, 206):
                            raise RuntimeError(f"HTTP {status}")
                        buf = bytearray()
                        while len(buf) < size:
                            self._check_cancel(cancel_event)
                            part = response.read(min(64 * 1024, size - len(buf)))
                            if not part:
                                break
                            buf.extend(part)
                            if emit and direct:
                                emit("正在直链下载分类资源", f"{h} {len(buf) / 1048576:.1f} MiB")
                    if len(buf) != size:
                        raise RuntimeError(f"下载不完整: {len(buf)}/{size}")
                    if expected_md5:
                        got = hashlib.md5(bytes(buf)).hexdigest()
                        if got != expected_md5:
                            raise RuntimeError(f"MD5 校验失败: {got} != {expected_md5}")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(bytes(buf))
                    written.append(dest)
                    done += size
                    if emit:
                        emit("正在下载分类资源", f"{done / 1048576:.1f} / {total / 1048576:.1f} MiB")
                    break
                except RuntimeError:
                    raise
                except Exception as ex:  # noqa: BLE001
                    last_err = ex
                    if attempt + 1 < _ATTEMPTS:
                        self._wait_retry(cancel_event, 1 + attempt * 2)
            else:
                raise RuntimeError(f"下载 {n} 失败: {last_err}")

        if emit:
            emit("分类资源下载完成", f"{len(written)} 个 bundle")
        return written

    @staticmethod
    def _check_cancel(cancel_event) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("已取消")

    @staticmethod
    def _wait_retry(cancel_event, seconds: float) -> None:
        if cancel_event is not None:
            if cancel_event.wait(seconds):
                raise RuntimeError("已取消")
        else:
            time.sleep(seconds)

    def _get(self, url: str, *, offset: int | None = None, cancel_event=None) -> bytes:
        last_err: Exception | None = None
        for attempt in range(_ATTEMPTS):
            self._check_cancel(cancel_event)
            try:
                headers = dict(_HEADERS)
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as response:
                    chunks = []
                    while True:
                        self._check_cancel(cancel_event)
                        part = response.read(64 * 1024)
                        if not part:
                            return b"".join(chunks)
                        chunks.append(part)
            except RuntimeError:
                raise
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt + 1 < _ATTEMPTS:
                    self._wait_retry(cancel_event, 1 + attempt * 2)
        raise RuntimeError(f"GET 失败 {url}: {last_err}")

    def list_versions(self) -> list[int]:
        body = self._get(self._latest_url(self._base(), "res_releases.json"))
        data = json.loads(body.decode("utf-8"))
        return [int(r["Version"]) for r in data.get("Releases", [])]

    def latest_version(self) -> int:
        body = self._get(self._latest_url(self._base(), "res_version.json"))
        return int(json.loads(body.decode("utf-8"))["Version"])

    def list_bundles(self) -> list[ChunkInfo]:
        if not self._chunks:
            raise RuntimeError("chunk 清单为空：请先导入 resources/metadata/chunk_list.txt")
        return list(self._chunks.values())

    def download(self, bundle: ChunkInfo, out_dir: str, cancel_event=None) -> Path:
        return self.download_chunk(bundle.name, Path(out_dir), cancel_event)

    def set_chunk_list(self, names_sizes: dict[str, int]) -> None:
        self._chunks = {
            n: ChunkInfo(name=n, url=self._chunk_url(self._base(), n), size=s)
            for n, s in names_sizes.items()
            if _CHUNK_RE.fullmatch(n)
        }

    def load_chunk_list_file(self, path: str | Path) -> int:
        p = Path(path)
        if not p.exists():
            return 0
        names: dict[str, int] = {}
        for line in p.read_text(encoding="utf-8").splitlines():
            name = line.strip()
            if _CHUNK_RE.fullmatch(name):
                names.setdefault(name, 0)
        self.set_chunk_list(names)
        return len(names)

    def ensure_chunk_list(self) -> int:
        default = Path(__file__).resolve().parents[2] / "resources" / "metadata" / "chunk_list.txt"
        return self.load_chunk_list_file(default)

    def download_patch_entry(
        self,
        record: dict,
        cancel_event=None,
        emit: Callable[[str, str], None] | None = None,
    ) -> bytes:
        """用 HTTP Range 只下载补丁 ZIP 中一个 entry，并解压、校验 CRC。"""
        pair = str(record.get("patch") or "")
        offset = int(record.get("data_offset") or 0)
        compressed = int(record.get("compressed_size") or 0)
        method = int(record.get("method") or 0)
        expected_size = int(record.get("file_size") or 0)
        expected_crc = int(record.get("crc") or 0)
        if not re.fullmatch(r"\d+_\d+", pair) or offset <= 0 or compressed <= 0:
            raise RuntimeError("补丁资源索引条目无效")
        host = self._base(cancel_event)
        url = f"{host}/{REGION}/{PLATFORM}/tags/major/{MAJOR}/patches/{pair}.patch"
        headers = dict(_HEADERS)
        headers["Range"] = f"bytes={offset}-{offset + compressed - 1}"
        request = urllib.request.Request(url, headers=headers)
        self._check_cancel(cancel_event)
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            status = getattr(response, "status", response.getcode())
            if status not in (200, 206):
                raise RuntimeError(f"补丁 Range 下载失败: HTTP {status}")
            data = bytearray()
            while len(data) < compressed:
                self._check_cancel(cancel_event)
                chunk = response.read(min(64 * 1024, compressed - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if emit:
                    emit("正在下载补丁资源", f"{record.get('name', '')} {len(data) / 1048576:.1f} / {compressed / 1048576:.1f} MiB")
        if len(data) != compressed:
            raise RuntimeError(f"补丁资源下载不完整: {len(data)}/{compressed}")
        if method == 0:
            output = bytes(data)
        elif method == 8:
            output = zlib.decompress(bytes(data), -15)
        else:
            raise RuntimeError(f"不支持的 ZIP 压缩方式: {method}")
        if expected_size and len(output) != expected_size:
            raise RuntimeError(f"补丁资源解压大小错误: {len(output)}/{expected_size}")
        if expected_crc and (zlib.crc32(output) & 0xFFFFFFFF) != expected_crc:
            raise RuntimeError("补丁资源 CRC 校验失败")
        return output

    def download_chunk_range(
        self,
        name: str,
        offset: int,
        length: int,
        cancel_event=None,
        emit: Callable[[str, str], None] | None = None,
        label: str = "",
    ) -> bytes:
        """用 HTTP Range 只取 chunk 内一个块（含 50B 块前缀），供跨 chunk 家族补取。

        清单里的 Length 已是「50B 前缀 + UnityFS 整块」长度，调用方直接交给
        split_ptn_chunk.read_block(raw, 0) 解密即可。
        """
        if not _CHUNK_RE.fullmatch(name):
            raise RuntimeError(f"chunk 名无效: {name}")
        if offset < 0 or length <= 0:
            raise RuntimeError(f"chunk 范围无效: {offset}+{length}")
        url = self._chunk_url(self._base(cancel_event), name)
        last_err: Exception | None = None
        for attempt in range(_ATTEMPTS):
            self._check_cancel(cancel_event)
            try:
                headers = dict(_HEADERS)
                headers["Range"] = f"bytes={offset}-{offset + length - 1}"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as response:
                    status = getattr(response, "status", response.getcode())
                    if status not in (200, 206):
                        raise RuntimeError(f"HTTP {status}")
                    data = bytearray()
                    while len(data) < length:
                        self._check_cancel(cancel_event)
                        part = response.read(min(64 * 1024, length - len(data)))
                        if not part:
                            break
                        data.extend(part)
                        if emit:
                            emit(
                                "正在下载 Live2D 家族资源",
                                f"{label or name} {len(data) / 1048576:.1f} / {length / 1048576:.1f} MiB",
                            )
                if len(data) != length:
                    # CDN 对不存在的路径返回 200/206 + 三字节 "404" 文本
                    raise RuntimeError(f"下载不完整: {len(data)}/{length}")
                return bytes(data)
            except RuntimeError:
                raise
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt + 1 < _ATTEMPTS:
                    self._wait_retry(cancel_event, 1 + attempt * 2)
        raise RuntimeError(f"下载 {name} 范围失败: {last_err}")

    def download_chunk(
        self,
        name: str,
        out_dir: Path,
        cancel_event=None,
        emit: Callable[[str, str], None] | None = None,
    ) -> Path:
        """断点下载单个 chunk；循环读取时检查取消事件并推送进度。"""
        if not _CHUNK_RE.fullmatch(name):
            raise RuntimeError(f"chunk 名无效: {name}")
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        dest = out / name
        part = dest.with_suffix(dest.suffix + ".part")
        offset = part.stat().st_size if part.exists() else 0
        url = self._chunk_url(self._base(cancel_event), name)
        last_err: Exception | None = None
        for attempt in range(_ATTEMPTS):
            self._check_cancel(cancel_event)
            try:
                headers = dict(_HEADERS)
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as response:
                    status = getattr(response, "status", response.getcode())
                    if status not in (200, 206):
                        raise RuntimeError(f"HTTP {status}")
                    mode = "ab" if offset and status == 206 else "wb"
                    if mode == "wb":
                        offset = 0
                    total_header = int(response.headers.get("Content-Length") or 0)
                    total = offset + total_header if total_header else 0
                    with open(part, mode) as stream:
                        while True:
                            self._check_cancel(cancel_event)
                            data = response.read(64 * 1024)
                            if not data:
                                break
                            stream.write(data)
                            offset += len(data)
                            if emit:
                                detail = f"{name} {offset / 1048576:.1f} MiB"
                                if total:
                                    detail += f" / {total / 1048576:.1f} MiB"
                                emit("正在下载 CDN chunk", detail)
                part.replace(dest)
                return dest
            except RuntimeError:
                raise
            except Exception as e:  # noqa: BLE001
                last_err = e
                offset = part.stat().st_size if part.exists() else 0
                if attempt + 1 < _ATTEMPTS:
                    self._wait_retry(cancel_event, 1 + attempt * 2)
        raise RuntimeError(f"下载 {name} 失败: {last_err}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    source = PtnCdnSource()
    print("最新资源版本:", source.latest_version())
    print("可用版本数:", len(source.list_versions()))
