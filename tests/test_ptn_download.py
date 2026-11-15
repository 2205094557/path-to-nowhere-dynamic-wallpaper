from __future__ import annotations

import json
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.sources.ptn_cdn import (
    load_library_state,
    resolve_live2d_target,
    resolve_static_target,
)
from tools.assemble_live2d import _family_member
from tools.split_ptn_chunk import HDR, _xor, iter_blocks


class TargetResolutionTests(unittest.TestCase):
    def setUp(self):
        self.state = {
            "static_best": {
                "lihui_Zoya_Default": {
                    "w": 2040,
                    "h": 1632,
                    "chunk": "res_11111111111111111111111111111111.bin",
                }
            },
            "moc3_bundles": {
                "Zoya_501": {
                    "bundle": "res_22222222222222222222222222222222.bin",
                    "size": 123,
                },
                "Zoya_502": {
                    "bundle": "res_33333333333333333333333333333333.bin",
                    "size": 456,
                },
            },
        }

    def test_static_target_casefold_lookup(self):
        target = resolve_static_target(
            {"type": "static", "painting": "lihui_zoya_default"}, self.state
        )
        self.assertEqual(target.asset_name, "lihui_Zoya_Default")
        self.assertEqual(target.chunk, "res_11111111111111111111111111111111.bin")

    def test_live2d_prefers_internal_model(self):
        target = resolve_live2d_target(
            {
                "type": "live2d",
                "name": "装束名",
                "painting": "zoya-special",
                "internal_model": "zoya_501",
            },
            self.state,
        )
        self.assertEqual(target.model_name, "Zoya_501")
        self.assertEqual(target.painting, "zoya-special")

    def test_live2d_ambiguous_fuzzy_match_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "不唯一"):
            resolve_live2d_target(
                {"type": "live2d", "name": "Zoya", "painting": "zoya"},
                self.state,
            )

    def test_invalid_chunk_name_is_rejected(self):
        state = {
            "static_best": {"lihui_a_b": {"chunk": "../bad.bin"}},
            "moc3_bundles": {},
        }
        with self.assertRaisesRegex(RuntimeError, "chunk 名无效"):
            resolve_static_target(
                {"type": "static", "painting": "lihui_a_b"}, state
            )

    def test_library_state_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text(json.dumps(self.state), encoding="utf-8")
            loaded = load_library_state(path)
            self.assertEqual(len(loaded["moc3_bundles"]), 2)


class ChunkStreamingTests(unittest.TestCase):
    @staticmethod
    def make_block(payload: bytes, key: int = 0x42) -> bytes:
        fs = 128 + len(payload)
        body = bytearray(fs)
        body[: len(HDR)] = HDR
        body[30:38] = struct.pack(">Q", fs)
        body[128:] = payload
        encrypted = _xor(bytes(body), key)
        # 50B 前缀内容不参与拆分。
        return bytes(50) + encrypted

    def test_iter_blocks_streams_multiple_blocks(self):
        raw = self.make_block(b"one") + self.make_block(b"two", 0x31)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "chunk.bin"
            path.write_bytes(raw)
            blocks = list(iter_blocks(path))
        self.assertEqual(len(blocks), 2)
        self.assertTrue(blocks[0][0].endswith(b"one"))
        self.assertEqual(blocks[-1][1], len(raw))

    def test_iter_blocks_honors_cancel(self):
        event = threading.Event()
        event.set()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "chunk.bin"
            path.write_bytes(self.make_block(b"cancel"))
            with self.assertRaisesRegex(RuntimeError, "已取消"):
                list(iter_blocks(path, event))


class Live2DFamilyParsingTests(unittest.TestCase):
    def test_family_suffixes(self):
        self.assertEqual(
            _family_member(
                "assets/art_live2d_characters_zoya_501_zoya_501_motion.bundle"
            ),
            ("characters_zoya_501_zoya_501", "motion"),
        )
        self.assertEqual(
            _family_member(
                "assets/art_live2d_characters_zoya_501_zoya_501.4096.bundle"
            ),
            ("characters_zoya_501_zoya_501", "tex"),
        )


class DirectClassifyTests(unittest.TestCase):
    def test_direct_url_uses_entry_version(self):
        from plugins.sources.ptn_cdn import PtnCdnSource

        src = PtnCdnSource()
        url = src._direct_classify_url(
            "https://example.test", "/assets/bc/bc6e981c77310880.bundle", 5
        )
        self.assertEqual(
            url,
            "https://example.test/release-cn/android/tags/major/2.5/"
            "classify/5/classify/assets/bc/bc6e981c77310880.bundle",
        )

    def test_download_manifest_keeps_per_bundle_version(self):
        from plugins.sources.ptn_cdn import PtnCdnSource

        src = PtnCdnSource()
        entry = src._download_manifest().get("/assets/bc/bc6e981c77310880.bundle")
        if entry is None:
            self.skipTest("直链清单未生成")
        self.assertEqual(entry["version"], "5")
        self.assertEqual(entry["size"], 3693509)


if __name__ == "__main__":
    unittest.main()
