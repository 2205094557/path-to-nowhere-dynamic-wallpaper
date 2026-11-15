# -*- coding: utf-8 -*-
"""M6 packaged release smoke test.

Checks the onedir layout, starts the packaged executable without opening a real
window (by supplying a tiny ``webview`` shim), probes backend/static endpoints,
and exercises both wallpaper exporters with temporary local fixtures. It never
performs game/CDN downloads and removes every fixture it creates.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGE = ROOT / "dist" / "ptn-wallpaper"
BACKEND_URL = "http://127.0.0.1:8770"
STATIC_URL = "http://127.0.0.1:5174"


class Checks:
    def __init__(self) -> None:
        self.ok = True

    def check(self, name: str, passed: bool, detail: str = "") -> bool:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))
        self.ok = self.ok and passed
        return passed


def get_json(url: str, timeout: float = 3.0) -> dict:
    req = urllib.request.Request(url, headers={"Host": "127.0.0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_bytes(url: str, timeout: float = 3.0) -> tuple[int, str, bytes]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.status, response.headers.get_content_type(), response.read()


def wait_for(url: str, timeout: float) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return get_json(url)
        except (OSError, ValueError, urllib.error.URLError):
            time.sleep(0.2)
    return None


def port_available(port: int) -> bool:
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def find_internal(package: Path) -> Path:
    return package / "_internal"


def verify_layout(checks: Checks, package: Path) -> tuple[Path, Path]:
    internal = find_internal(package)
    exe = package / "ptn-wallpaper.exe"
    required = {
        "executable": exe,
        "frontend index": internal / "frontend" / "dist" / "index.html",
        "Live2D browser core": internal / "frontend" / "dist" / "vendor" / "live2dcubismcore.min.js",
        "templates": internal / "templates" / "wallpaper_static.html",
        "plugins": internal / "plugins" / "exporters" / "wallpaper_static.py",
        "core runtime": internal / "core" / "registry.py",
        "tools": internal / "tools" / "build_local_index.py",
        "characters metadata": internal / "resources" / "metadata" / "characters.json",
        "skins metadata": internal / "resources" / "metadata" / "skins.json",
        "BWiki skin baseline": internal / "resources" / "metadata" / "skins_bwiki.json",
        "CDN chunk list": internal / "resources" / "metadata" / "chunk_list.txt",
        "resource locator index": internal / "resources" / "metadata" / "library_state.json",
        "Live2D model inventory": internal / "resources" / "metadata" / "live2d_inventory.json",
        "Live2D family index": internal / "resources" / "metadata" / "live2d_family_bundles.json",
        "classify slice index": internal / "resources" / "metadata" / "classify_index.json",
        "classify direct manifest": internal / "resources" / "metadata" / "classify_download_manifest.json",
        "classify group map": internal / "resources" / "metadata" / "classify_group_map.json",
        "PTN render profiles": internal / "resources" / "metadata" / "ptn_render_profiles.json",
        "patch resource index": internal / "resources" / "metadata" / "patch_resource_index.json",
        "name aliases": internal / "resources" / "metadata" / "name_aliases.json",
        "Live2D extractor reference": internal / "references" / "UnityPyLive2DExtractor" / "UnityPyLive2DExtractor" / "__main__.py",
    }
    for name, path in required.items():
        checks.check(f"package contains {name}", path.exists(), str(path))

    forbidden = {
        "local download index": internal / "resources" / "metadata" / "local_skins.json",
        "metadata response cache": internal / "resources" / "metadata" / "bwiki_responses",
        "runtime library database": internal / "resources" / "library.db",
        "bundles": internal / "resources" / "bundles",
        "extracted assets": internal / "resources" / "extracted",
        "exported wallpapers": internal / "resources" / "wallpapers",
    }
    for name, path in forbidden.items():
        checks.check(f"package excludes {name}", not path.exists(), str(path))

    for filename in ("characters.json", "skins.json"):
        path = internal / "resources" / "metadata" / filename
        valid = False
        detail = ""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            valid = isinstance(payload, (list, dict))
            detail = f"{path.stat().st_size} bytes"
        except Exception as exc:  # noqa: BLE001
            detail = str(exc)
        checks.check(f"valid {filename}", valid, detail)
    return exe, internal


def make_webview_shim() -> tuple[Path, dict[str, str]]:
    temp = Path(tempfile.mkdtemp(prefix="ptn-verify-shim-"))
    (temp / "webview.py").write_text(
        """import time
class _Event:
    def __iadd__(self, fn):
        return self
class _Events:
    def __init__(self):
        self.resized = self.maximized = self.restored = self.closing = _Event()
class _Window:
    width = 1200
    height = 700
    events = _Events()
def create_window(*args, **kwargs):
    return _Window()
def start(*args, **kwargs):
    time.sleep(float(__import__('os').environ.get('PTN_VERIFY_HOLD', '20')))
""",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(temp)
    env["PTN_VERIFY_HOLD"] = "20"
    return temp, env


def stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def verify_services(checks: Checks, exe: Path, internal: Path, timeout: float) -> None:
    if not exe.exists():
        return
    if not port_available(8770) or not port_available(5174):
        checks.check("smoke-test ports are free", False, "8770 or 5174 is already in use")
        return

    shim_dir, env = make_webview_shim()
    proc = subprocess.Popen(
        [str(exe)],
        cwd=str(exe.parent),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        health = wait_for(f"{BACKEND_URL}/api/health", timeout)
        checks.check("packaged backend health", bool(health and health.get("ok")), str(health or {}))
        if health:
            plugins = health.get("plugins") or {}
            checks.check("source plugin discovered", "ptn_cdn" in plugins.get("sources", []), str(plugins))
            checks.check(
                "extractor plugins discovered",
                {"live2d", "static"}.issubset(set(plugins.get("extractors", []))),
                str(plugins),
            )
            checks.check(
                "exporter plugins discovered",
                {"wallpaper_live2d", "wallpaper_static"}.issubset(set(plugins.get("exporters", []))),
                str(plugins),
            )

        try:
            status, content_type, index = get_bytes(f"{STATIC_URL}/")
            checks.check("static frontend index", status == 200 and b'<div id="app"></div>' in index, content_type)
        except Exception as exc:  # noqa: BLE001
            checks.check("static frontend index", False, str(exc))
        try:
            status, content_type, characters = get_bytes(f"{STATIC_URL}/resources/metadata/characters.json")
            parsed = json.loads(characters.decode("utf-8"))
            checks.check("static metadata route", status == 200 and isinstance(parsed, (list, dict)), content_type)
        except Exception as exc:  # noqa: BLE001
            checks.check("static metadata route", False, str(exc))
        try:
            state_status, state_type, state_body = get_bytes(
                f"{STATIC_URL}/resources/metadata/library_state.json"
            )
            state = json.loads(state_body.decode("utf-8"))
            valid_state = (
                state_status == 200
                and isinstance(state.get("static_best"), dict)
                and isinstance(state.get("moc3_bundles"), dict)
                and bool(state.get("static_best"))
            )
            checks.check("static resource locator route", valid_state, state_type)
        except Exception as exc:  # noqa: BLE001
            checks.check("static resource locator route", False, str(exc))
        try:
            status, content_type, vendor = get_bytes(
                f"{STATIC_URL}/vendor/live2dcubismcore.min.js"
            )
            checks.check("static frontend asset", status == 200 and len(vendor) > 1000, content_type)
        except Exception as exc:  # noqa: BLE001
            checks.check("static frontend asset", False, str(exc))

        runtime_db = internal / "resources" / "library.db"
        checks.check("runtime library database initialized", runtime_db.is_file())
        local_index = internal / "resources" / "metadata" / "local_skins.json"
        runtime_dirs = [internal / "resources" / name for name in ("bundles", "extracted", "wallpapers", "exports")]
        checks.check("runtime local index initialized", local_index.is_file())
        checks.check("runtime resource directories initialized", all(path.is_dir() for path in runtime_dirs))
    finally:
        stop_process(proc)
        shutil.rmtree(shim_dir, ignore_errors=True)
        # Restore release-clean state after testing the mutable onedir package.
        (internal / "resources" / "library.db").unlink(missing_ok=True)
        (internal / "resources" / "metadata" / "local_skins.json").unlink(missing_ok=True)
        for path in (internal / "resources" / name for name in ("bundles", "extracted", "wallpapers", "exports")):
            shutil.rmtree(path, ignore_errors=True)


def one_pixel_png() -> bytes:
    import base64

    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+XhVfWQAAAABJRU5ErkJggg=="
    )


def verify_exporters(checks: Checks, package_root: Path) -> None:
    """Import packaged plugin source files and produce minimal static/Live2D projects."""
    old_modules = {
        name: module
        for name, module in list(sys.modules.items())
        if name == "core" or name.startswith("core.") or name == "plugins" or name.startswith("plugins.")
    }
    for name in old_modules:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(package_root))
    try:
        from core.registry import Registry

        registry = Registry(package_root / "plugins").discover()
        with tempfile.TemporaryDirectory(prefix="ptn-export-smoke-") as temp_name:
            temp = Path(temp_name)
            resources = temp / "resources"
            output = temp / "output"
            static_asset = resources / "fixture" / "static"
            static_asset.mkdir(parents=True)
            (static_asset / "painting.png").write_bytes(one_pixel_png())
            static_skin = {
                "character": "M6",
                "name": "Static",
                "type": "static",
                "asset": {"dir": "fixture/static", "image": "painting.png"},
            }
            static_exporter = registry.get("exporters", "wallpaper_static")
            static_project = Path(
                static_exporter.export(static_skin, {"root": str(temp), "bg": "monet"}, str(output))
            )
            static_ok = all(
                (static_project / rel).is_file()
                for rel in ("index.html", "project.json", "preview.jpg", "assets/painting.png")
            )
            checks.check("static exporter smoke", static_ok, str(static_project))

            live_asset = resources / "fixture" / "live2d"
            live_asset.mkdir(parents=True)
            (live_asset / "fixture.moc3").write_bytes(b"MOC3")
            (live_asset / "texture.png").write_bytes(one_pixel_png())
            model = {
                "Version": 3,
                "FileReferences": {"Moc": "fixture.moc3", "Textures": ["texture.png"]},
            }
            (live_asset / "fixture.model3.json").write_text(json.dumps(model), encoding="utf-8")
            live_skin = {
                "character": "M6",
                "name": "Live2D",
                "type": "live2d",
                "asset": {"dir": "fixture/live2d", "model": "fixture.model3.json"},
            }
            live_exporter = registry.get("exporters", "wallpaper_live2d")
            live_project = Path(
                live_exporter.export(live_skin, {"root": str(temp), "bg": "monet", "track": True}, str(output))
            )
            live_ok = all(
                (live_project / rel).is_file()
                for rel in (
                    "index.html",
                    "project.json",
                    "preview.jpg",
                    "live2dcubismcore.min.js",
                    "live2d-app.js",
                    "wallpaper-layout.js",
                    "assets/model/fixture.model3.json",
                )
            )
            checks.check("Live2D exporter smoke", live_ok, str(live_project))
    except Exception as exc:  # noqa: BLE001
        checks.check("exporter smoke setup", False, f"{type(exc).__name__}: {exc}")
    finally:
        try:
            sys.path.remove(str(package_root))
        except ValueError:
            pass
        for name in list(sys.modules):
            if name == "core" or name.startswith("core.") or name == "plugins" or name.startswith("plugins."):
                sys.modules.pop(name, None)
        sys.modules.update(old_modules)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify the packaged PTN onedir release")
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE, help="dist/ptn-wallpaper directory")
    parser.add_argument("--timeout", type=float, default=20.0, help="backend startup timeout in seconds")
    parser.add_argument(
        "--layout-only",
        action="store_true",
        help="check package files only; do not start the executable or run exporters",
    )
    # Backward-compatible no-op: verification always cleans its own fixtures.
    parser.add_argument("--clean", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    package = args.package.resolve()
    checks = Checks()
    checks.check("package directory exists", package.is_dir(), str(package))
    if not package.is_dir():
        return 1
    exe, internal = verify_layout(checks, package)
    if not args.layout_only:
        verify_exporters(checks, internal)
        verify_services(checks, exe, internal, args.timeout)
    print("\n" + ("Package verification passed." if checks.ok else "Package verification FAILED."))
    return 0 if checks.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
