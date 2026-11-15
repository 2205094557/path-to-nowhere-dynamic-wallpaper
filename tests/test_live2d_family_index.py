from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import assemble_live2d
from tools.build_live2d_family_index import (
    Manifest,
    _family_bundles,
    _path_id,
    build,
)


class BundlePathTests(unittest.TestCase):
    def test_path_id_matches_official_manifest(self):
        # 实测值：官方清单里这三个 bundle 的 /assets/<id>.bundle 前缀
        self.assertEqual(
            _path_id("assets/art_live2d_characters_hameier_5.bundle"), "d99a00b70b9440e2"
        )
        self.assertEqual(
            _path_id("assets/art_live2d_characters_hameier_5_animation.bundle"),
            "235ebf9ffb243ff3",
        )
        self.assertEqual(
            _path_id("assets/art_live2d_characters_hameier_5_hameier_5.4096.bundle"),
            "31b618066c567f84",
        )

    def test_family_bundle_names_repeat_model_for_texture(self):
        names = _family_bundles(
            "Hameier_5", "assets/art_live2d_characters_hameier_5.bundle"
        )
        self.assertIn("assets/art_live2d_characters_hameier_5_material.bundle", names)
        self.assertIn(
            "assets/art_live2d_characters_hameier_5_hameier_5.4096.bundle", names
        )


class FamilyIndexBuildTests(unittest.TestCase):
    def test_only_bundles_outside_moc_chunk_are_indexed(self):
        from tools.build_live2d_family_index import _path

        moc = "assets/art_live2d_characters_hameier_5.bundle"
        texture = "assets/art_live2d_characters_hameier_5_hameier_5.4096.bundle"
        same_chunk = "assets/art_live2d_characters_hameier_5_animation.bundle"
        manifest = Manifest(
            {
                "ChunkPatches": [
                    {"Filename": "res_" + "1" * 32 + ".bin"},
                    {"Filename": "res_" + "2" * 32 + ".bin"},
                ],
                "Entries": [
                    {"Path": _path(moc), "ChunkIndex": 0, "Offset": 0, "Length": 100},
                    {"Path": _path(same_chunk), "ChunkIndex": 0, "Offset": 200, "Length": 50},
                    {"Path": _path(texture), "ChunkIndex": 1, "Offset": 300, "Length": 900},
                ],
            }
        )
        state = {"moc3_bundles": {"Hameier_5": {"bundle": "x", "size": 1}}}
        result = build(manifest, state)
        bundles = result["models"]["Hameier_5"]["bundles"]
        self.assertEqual(len(bundles), 1)
        self.assertEqual(bundles[0]["name"], texture)
        self.assertEqual(bundles[0]["offset"], 300)

    def test_models_without_family_are_omitted(self):
        manifest = Manifest({"ChunkPatches": [], "Entries": []})
        result = build(manifest, {"moc3_bundles": {"Chain_2": {"bundle": "x", "size": 1}}})
        self.assertEqual(result["models"], {})


class ExtraBlockTests(unittest.TestCase):
    def test_assemble_from_chunk_appends_extra_blocks(self):
        captured: dict = {}
        original = assemble_live2d.assemble_live2d

        def fake(model, blocks, out_dir, **kwargs):
            captured["model"] = model
            captured["blocks"] = [data for data, _ in blocks()]
            return {"model": model}

        assemble_live2d.assemble_live2d = fake
        try:
            with tempfile.TemporaryDirectory() as temp:
                chunk = Path(temp) / ("res_" + "a" * 32 + ".bin")
                chunk.write_bytes(b"")
                assemble_live2d.assemble_from_chunk(
                    "Hameier_5",
                    chunk,
                    Path(temp) / "out",
                    extra_blocks=[(b"extra-1", 0), (b"extra-2", 0)],
                )
        finally:
            assemble_live2d.assemble_live2d = original
        self.assertEqual(captured["model"], "Hameier_5")
        self.assertEqual(captured["blocks"], [b"extra-1", b"extra-2"])


class FamilyIndexFileTests(unittest.TestCase):
    def test_repository_index_covers_hameier_5(self):
        path = ROOT / "resources" / "metadata" / "live2d_family_bundles.json"
        if not path.is_file():
            self.skipTest("家族索引未生成")
        payload = json.loads(path.read_text(encoding="utf-8"))
        bundles = payload["models"]["Hameier_5"]["bundles"]
        names = {item["name"] for item in bundles}
        self.assertTrue(
            any(name.endswith("_hameier_5.4096.bundle") for name in names), names
        )


if __name__ == "__main__":
    unittest.main()
