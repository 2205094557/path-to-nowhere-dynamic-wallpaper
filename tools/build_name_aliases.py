# -*- coding: utf-8 -*-
"""从 PtNData 社区数据生成资源名别名表 name_aliases.json。

数据源：gilmarion/PtNData 的 Sinner_Stuff/CN/Hero_Profile.json
（字段：姓名=中文名，Internal Name=游戏资源内部命名，Sinner No=MBCC 编号）。
经 jsdelivr CDN 获取可规避 GitHub 直连问题与 BWiki 限流：
    https://cdn.jsdelivr.net/gh/gilmarion/PtNData@main/Sinner_Stuff/CN/Hero_Profile.json

生成的映射供 tools/build_ptn_metadata.py 把 lihui_{内部名}_{皮肤} 资源归到
BWiki 图鉴角色。特例（联动角色/局长/英文名差异角色）在此脚本内维护手工表。

用法：
    python tools/build_name_aliases.py [Hero_Profile.json 路径]
不提供路径时尝试 resources/metadata/hero_profile_cn.json。
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "resources" / "metadata"

DATA_URL = "https://cdn.jsdelivr.net/gh/gilmarion/PtNData@main/Sinner_Stuff/CN/Hero_Profile.json"

# 手工核实特例：中文名 -> 资源内部名
# （联动角色不在 PtNData 主表；player=玩家主角；Rust/Standby 等为官方英文名形态）
MANUAL = {
    "空条承太郎": "jotaro",   # JOJO 联动
    "空条徐伦": "jolyne",     # JOJO 联动
    "天气预报": "weather",    # JOJO 联动
    "局长": "player",         # 玩家主角，非禁闭者
    "锈": "rust",
    # 资源内部名为拼音形态的角色（PtNData 内部名用英文名，与资源命名不一致）
    "霁川": "jichuan",
    "克莱门汀": "kelaimenting",
    "诺玛": "nuoma",
    "皮克赛尔": "pikesaier",
    "席宁": "xining",
    "墨菲": "mofei",
    "啸风": "xiaofeng",
}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    MD.mkdir(parents=True, exist_ok=True)
    local = MD / "hero_profile_cn.json"

    if len(sys.argv) > 1:
        src = Path(sys.argv[1])
        data = json.loads(src.read_text(encoding="utf-8"))
    elif local.exists():
        data = json.loads(local.read_text(encoding="utf-8"))
    else:
        print("下载 PtNData Hero_Profile.json …")
        req = urllib.request.Request(DATA_URL, headers={"User-Agent": "ptn-wallpaper/0.1"})
        with urllib.request.urlopen(req, timeout=60) as r:
            local.write_bytes(r.read())
        data = json.loads(local.read_text(encoding="utf-8"))

    aliases: dict[str, str] = {}
    for item in data.values():
        cn = str(item.get("姓名", "")).strip()
        internal = str(item.get("Internal Name", "")).strip().lower()
        if cn and internal:
            aliases[cn] = internal
    aliases.update(MANUAL)

    out = MD / "name_aliases.json"
    out.write_text(json.dumps(aliases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"name_aliases.json: {len(aliases)} 条 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
