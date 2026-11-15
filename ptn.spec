# -*- mode: python ; coding: utf-8 -*-
"""Path to Nowhere desktop build: onedir with only runtime code and small catalog data."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


ROOT = Path(SPECPATH)


def existing_data(source: str, destination: str) -> list[tuple[str, str]]:
    path = ROOT / source
    return [(str(path), destination)] if path.exists() else []


def collect_required(package: str):
    """Collect package data/binaries and imports used through runtime-loaded plugins."""
    return collect_all(package)


# Do not add resources/bundles, resources/extracted, resources/wallpapers, metadata
# response caches, or local library state here. A release must start from an empty
# download state and must not absorb multi-gigabyte developer data.
datas: list[tuple[str, str]] = []
datas += existing_data("templates", "templates")
datas += existing_data("frontend/dist", "frontend/dist")
datas += existing_data("plugins", "plugins")
datas += existing_data("core", "core")
datas += existing_data("tools", "tools")
datas += existing_data(
    "references/UnityPyLive2DExtractor/UnityPyLive2DExtractor",
    "references/UnityPyLive2DExtractor/UnityPyLive2DExtractor",
)
datas += existing_data(
    "references/UnityPyLive2DExtractor/external",
    "references/UnityPyLive2DExtractor/external",
)
datas += existing_data("resources/config.json", "resources")

METADATA_FILES = (
    "characters.json",
    "skins.json",
    "skins_bwiki.json",
    "hero_profile_cn.json",
    "en_aliases.json",
    "name_aliases.json",
    "live2d_inventory.json",
    "live2d_family_bundles.json",
    "classify_index.json",
    "classify_download_manifest.json",
    "classify_group_map.json",
    "ptn_render_profiles.json",
    "patch_resource_index.json",
    "resource_matching.json",
    "library_state.json",
    "res_base_classify_download.json",
    "res_install.json",
    "chunk_list.txt",
    "avatars.json",
)
for filename in METADATA_FILES:
    datas += existing_data(f"resources/metadata/{filename}", "resources/metadata")

binaries = []
hiddenimports = [
    "core.registry",
    "core.metadata",
    "core.library",
    "core.palette",
    "core.we_integration",
    "core.wallpaper",
    "plugins.sources.ptn_cdn",
    "plugins.extractors.live2d",
    "plugins.extractors.static",
    "plugins.exporters.wallpaper_live2d",
    "plugins.exporters.wallpaper_static",
    "requests",
    "PIL",
    "coloredlogs",
    "humanfriendly",
    "sssekai",
    "sssekai.fmt.motion3",
    "sssekai.fmt.moc3",
    "sssekai.unity",
    "sssekai.unity.AnimationClip",
]

# UnityPy carries native decoders and pypinyin carries dictionary data. The other
# runtime modules above need only their explicitly named imports; collecting their
# whole packages would also ship unrelated CLIs/tests (including Spine tooling).
for package in ("UnityPy", "pypinyin"):
    package_datas, package_bins, package_hidden = collect_required(package)
    datas += package_datas
    binaries += package_bins
    hiddenimports += package_hidden


a = Analysis(
    ["app_pack.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "numpy"],
    noarchive=False,
)
pyz = PYZ(a.pure)

icon_path = ROOT / "resources" / "icon.ico"
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ptn-wallpaper",
    icon=str(icon_path) if icon_path.exists() else None,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ptn-wallpaper",
)
