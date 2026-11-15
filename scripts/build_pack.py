# -*- coding: utf-8 -*-
"""一键打包脚本：依赖预检 → packaged 前端构建 → PyInstaller → 产物自检。

用法：
  python scripts/build_pack.py

打包版固定使用后端 8770；本脚本每次都以 packaged 模式重建前端，并检查
最终 JS 没有回退到开发端口 8766。发布包仅包含图鉴 JSON 和必要运行时资源，
不会包含开发机上的 bundles/extracted/wallpapers 等大目录。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
VITE_JS = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
PYINSTALLER = ROOT / ".venv" / "Scripts" / "pyinstaller.exe"
SPEC = ROOT / "ptn.spec"
DIST_DIR = ROOT / "dist" / "ptn-wallpaper"
INTERNAL = DIST_DIR / "_internal"
REQUIRED_MODULES = ("PyInstaller", "webview", "UnityPy", "PIL", "requests", "pypinyin", "sssekai", "coloredlogs")


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    print(">>", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, timeout=timeout)


def check(name: str, ok: bool, extra: str = "") -> bool:
    print(("  [PASS] " if ok else "  [FAIL] ") + name + (("  " + extra) if extra else ""))
    return ok


def assets_have(dist: Path, text: str) -> bool:
    assets = dist / "assets"
    if not assets.is_dir():
        return False
    return any(
        text in p.read_text(encoding="utf-8", errors="ignore")
        for p in assets.glob("*.js")
    )


def packaged_app_running() -> bool:
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ptn-wallpaper.exe"],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            timeout=10,
        )
        return "ptn-wallpaper.exe" in r.stdout
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ok = True
    print("===== 一键打包 path-to-nowhere-dynamic-wallpaper =====\n")

    if packaged_app_running():
        print("[FAIL] 打包版正在运行，dist 会被占用。请先关闭 ptn-wallpaper.exe 再打包。")
        return 1

    # 1) 依赖与配置预检。Live2D 完整转换器会在运行时导入 sssekai/coloredlogs，
    # 缺失时不能继续生成一个表面成功、实际提取退化的安装包。
    ok = check("存在 ptn.spec", SPEC.is_file()) and ok
    missing = [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]
    ok = check("Python 打包依赖齐全", not missing, ", ".join(missing) if missing else "") and ok
    obsolete_pathlib = False
    pathlib_spec = importlib.util.find_spec("pathlib")
    if pathlib_spec and pathlib_spec.origin:
        origin = pathlib_spec.origin.replace("\\", "/").lower()
        obsolete_pathlib = "site-packages/pathlib" in origin
    ok = check(
        "未安装 pathlib 回移植包",
        not obsolete_pathlib,
        pathlib_spec.origin if obsolete_pathlib and pathlib_spec else "",
    ) and ok
    if missing or obsolete_pathlib or not SPEC.is_file():
        print("依赖或 spec 预检失败。请安装 requirements.txt；若提示 pathlib，请先卸载该回移植包。")
        return 1

    # 2) 前端构建：强制 packaged 模式
    if not VITE_JS.exists():
        print("[FAIL] 未找到 Vite，请先在 frontend 目录执行 npm install")
        return 1
    r = run(["node", str(VITE_JS), "build", "--mode", "packaged"], cwd=FRONTEND, timeout=300)
    ok = check("前端构建（--mode packaged）", r.returncode == 0) and ok

    # 3) 前端产物校验：必须有 8770，且无 8766
    src_dist = FRONTEND / "dist"
    ok = check("前端 API 指向 127.0.0.1:8770", assets_have(src_dist, "127.0.0.1:8770")) and ok
    ok = check("前端无 8766 残留", not assets_have(src_dist, "127.0.0.1:8766")) and ok

    if not ok:
        print("前端产物校验失败，停止 PyInstaller 构建。")
        return 1

    # 4) PyInstaller
    # 注意：.venv/Scripts/pyinstaller.exe 在部分机器上是坏入口（静默失败 exit 1），
    # 此时必须 fallback 到 python -m PyInstaller，否则打包直接失败。
    if PYINSTALLER.exists():
        r = run([str(PYINSTALLER), str(SPEC), "--noconfirm", "--clean"], cwd=ROOT)
        if r.returncode != 0:
            print("  [info] pyinstaller.exe 入口失效，改用 python -m PyInstaller")
            r = run([sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm", "--clean"], cwd=ROOT)
    else:
        r = run([sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm", "--clean"], cwd=ROOT)
    ok = check("PyInstaller 构建", r.returncode == 0) and ok
    if r.returncode != 0:
        print("PyInstaller 构建失败，停止产物自检。")
        return 1

    # 5) 产物静态自检
    required = {
        "exe": DIST_DIR / "ptn-wallpaper.exe",
        "plugins": INTERNAL / "plugins",
        "core": INTERNAL / "core",
        "tools": INTERNAL / "tools",
        "templates": INTERNAL / "templates",
        "frontend dist": INTERNAL / "frontend" / "dist",
        "vendor l2d core": INTERNAL / "frontend" / "dist" / "vendor" / "live2dcubismcore.min.js",
        "references l2d": INTERNAL / "references" / "UnityPyLive2DExtractor" / "UnityPyLive2DExtractor",
        "characters metadata": INTERNAL / "resources" / "metadata" / "characters.json",
        "skins metadata": INTERNAL / "resources" / "metadata" / "skins.json",
        "template live2d-app": INTERNAL / "templates" / "live2d-app.js",
    }
    for name, p in required.items():
        ok = check(f"产物包含 {name}", p.exists()) and ok
    forbidden = {
        "local_skins.json": INTERNAL / "resources" / "metadata" / "local_skins.json",
        "metadata cache": INTERNAL / "resources" / "metadata" / "bwiki_responses",
        "library.db": INTERNAL / "resources" / "library.db",
        "bundles": INTERNAL / "resources" / "bundles",
        "extracted": INTERNAL / "resources" / "extracted",
        "wallpapers": INTERNAL / "resources" / "wallpapers",
    }
    for name, p in forbidden.items():
        ok = check(f"产物不包含 {name}", not p.exists()) and ok

    # 6) 打包版前端 API 回归检查（最关键）
    packed_dist = INTERNAL / "frontend" / "dist"
    ok = check("打包版前端指向 127.0.0.1:8770", assets_have(packed_dist, "127.0.0.1:8770")) and ok
    ok = check("打包版前端无 8766 残留", not assets_have(packed_dist, "127.0.0.1:8766")) and ok

    print()
    if ok:
        print(f"构建自检全部通过，产物：{DIST_DIR}")
        print("发布前建议再跑：python tools/verify_package.py --clean")
        return 0
    print("存在 FAIL，禁止发布。请根据上方 FAIL 项修复后重新打包。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
