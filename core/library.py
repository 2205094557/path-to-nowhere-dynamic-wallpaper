"""本地资源库：SQLite 记录角色/装束/资源状态。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS characters(
  id TEXT PRIMARY KEY,
  name TEXT, profession TEXT, rarity TEXT, series TEXT, grade TEXT
);
CREATE TABLE IF NOT EXISTS skins(
  id TEXT PRIMARY KEY,
  character TEXT, name TEXT, bundle TEXT, type TEXT, status TEXT,
  asset_dir TEXT, asset_model TEXT, asset_layers TEXT
);
"""


class Library:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.executescript(SCHEMA)

    def import_metadata(self, characters: list[dict], skins: list[dict], local_skins: list[dict]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM characters")
            self.conn.execute("DELETE FROM skins")
            for c in characters:
                self.conn.execute(
                    "INSERT OR REPLACE INTO characters(id,name,profession,rarity,series,grade) VALUES(?,?,?,?,?,?)",
                    (
                        c.get("name"), c.get("name"), c.get("profession", ""),
                        c.get("rarity", ""), c.get("series", ""), c.get("grade", ""),
                    ),
                )
            for sk in skins:
                # 优先按 painting 精确匹配本地资源；同一 character+bundle 可能对应多个装束
                # （bundle 均为空的占位条目），此时不能按 character+bundle
                # 回退，否则会把基础装束的静态资源误挂到动态装束上。仅当该键唯一时才回退。
                painting = sk.get("painting") or ""
                loc = next(
                    (l for l in local_skins if (l.get("painting") or "") == painting),
                    None,
                )
                if loc is None:
                    same_key = [
                        s
                        for s in skins
                        if s.get("character") == sk.get("character") and s.get("bundle") == sk.get("bundle")
                    ]
                    if len(same_key) == 1:
                        loc = next(
                            (
                                l
                                for l in local_skins
                                if l.get("character") == sk.get("character") and l.get("bundle") == sk.get("bundle")
                            ),
                            None,
                        )
                asset = (loc or {}).get("asset") or {}
                # 主键加入 painting，避免同 character+bundle 的多个装束相互覆盖
                skin_id = f"{sk.get('character')}|{sk.get('bundle')}|{painting}"
                self.conn.execute(
                    "INSERT OR REPLACE INTO skins(id,character,name,bundle,type,status,asset_dir,asset_model,asset_layers) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        skin_id, sk.get("character"), sk.get("name"), sk.get("bundle", ""),
                        (loc or {}).get("type", "unknown"),
                        "downloaded" if loc else "remote",
                        asset.get("dir", ""),
                        asset.get("model", ""),
                        json.dumps(asset.get("layers", []), ensure_ascii=False),
                    ),
                )

    def list_characters(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT name,profession,rarity,series,grade FROM characters ORDER BY name"
        ).fetchall()
        return [
            {"name": r[0], "profession": r[1], "rarity": r[2], "series": r[3], "grade": r[4]}
            for r in rows
        ]

    def list_skins(self, character: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT name,bundle,type,status,asset_dir,asset_model,asset_layers FROM skins WHERE character=? ORDER BY bundle",
            (character,),
        ).fetchall()
        out = []
        for r in rows:
            entry = {"name": r[0], "bundle": r[1], "type": r[2], "status": r[3]}
            if r[4]:
                entry["asset"] = {"dir": r[4], "model": r[5], "layers": json.loads(r[6] or "[]")}
            else:
                entry["asset"] = None
            out.append(entry)
        return out

    def downloaded_skins(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT character,name,bundle,type FROM skins WHERE status='downloaded'"
        ).fetchall()
        return [{"character": r[0], "name": r[1], "bundle": r[2], "type": r[3]} for r in rows]

    def close(self) -> None:
        self.conn.close()
