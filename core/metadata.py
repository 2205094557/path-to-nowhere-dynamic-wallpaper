"""元数据服务：读取 resources/metadata（BWiki 同步的官方数据 + 本地资源索引）。

数据文件约定（由 tools/build_ptn_metadata.py 与 tools/build_local_index.py 生成）：
  - characters.json    角色表（name/profession/rarity/series/grade 等图鉴字段）
  - skins.json         装束表（character/bundle/name/painting/type/series 等）
  - local_skins.json   本地已提取资源索引（build_local_index 扫描产物生成）
"""

from __future__ import annotations

import json
from pathlib import Path


class Metadata:
    def __init__(self, metadata_dir: Path) -> None:
        self.dir = Path(metadata_dir)
        self._characters: list[dict] = []
        self._skins: list[dict] = []
        self._local: list[dict] = []
        self.reload()

    def reload(self) -> None:
        self._characters = self._read_list("characters.json", ("characters",), [])
        self._skins = self._read_list("skins.json", ("skins",), [])
        self._local = self._read("local_skins.json", [])
        if not isinstance(self._local, list):
            self._local = []

    def _read(self, name: str, default):
        p = self.dir / name
        if not p.exists():
            return default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return default

    def _read_list(self, name: str, wrap_keys: tuple[str, ...], default: list) -> list:
        """读列表型元数据：兼容裸数组与包装对象（如 {"characters": [...]}）两种落盘格式。"""
        data = self._read(name, default)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in wrap_keys:
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return default

    def characters(self) -> list[dict]:
        return self._characters

    def skins(self) -> list[dict]:
        return self._skins

    def local_skins(self) -> list[dict]:
        return self._local

    def skin_type(self, character: str, bundle: str) -> str | None:
        for loc in self._local:
            if loc.get("character") == character and loc.get("bundle") == bundle:
                return loc.get("type")
        return None
