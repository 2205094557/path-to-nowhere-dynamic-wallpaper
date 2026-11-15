from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import export_from_emulator as emu


class GroupIdTests(unittest.TestCase):
    def test_expected_group_id_uses_idnum_and_seq(self):
        gid = emu.expected_group_id({"painting": "hameier_5"}, {"110601": "hameier"})
        self.assertEqual(gid, "11060105")
        gid = emu.expected_group_id({"painting": "coquelicot_5"}, {"110604": "coquelicot"})
        self.assertEqual(gid, "11060405")

    def test_unknown_character_has_no_group(self):
        self.assertEqual(emu.expected_group_id({"painting": "player_3"}, {}), "")


class CatalogTests(unittest.TestCase):
    def test_load_catalog_accepts_flat_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "res_base_classify.json").write_text(
                json.dumps({"Live2d": {"11060105": {"Infos": []}}}), encoding="utf-8"
            )
            (root / "res_classify.json").write_text(
                json.dumps({"Infos": []}), encoding="utf-8"
            )
            catalog = emu.load_catalog(root)
        self.assertIn("11060105", catalog["groups"])

    def test_encrypted_manifest_is_rejected_with_hint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "res_base_classify.json").write_bytes(b"\xa3garbage")
            with self.assertRaisesRegex(RuntimeError, "不是明文清单"):
                emu.load_catalog(root)


class TargetSelectionTests(unittest.TestCase):
    def setUp(self):
        self._extracted = emu.EXTRACTED
        self._temp = tempfile.TemporaryDirectory()
        emu.EXTRACTED = Path(self._temp.name)
        (emu.EXTRACTED / "live2d" / "hameier_5").mkdir(parents=True)
        (emu.EXTRACTED / "live2d" / "hameier_5" / "model3.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        emu.EXTRACTED = self._extracted
        self._temp.cleanup()

    def test_missing_filter_and_availability(self):
        skins = [
            {"type": "live2d", "painting": "hameier_5", "character": "哈梅尔",
             "name": "依偎涟漪", "release_date": "2024-11-28"},
            {"type": "live2d", "painting": "coquelicot_5", "character": "可可莉克",
             "name": "绯夜初醒", "release_date": "2026-05-14"},
            {"type": "live2d", "painting": "ceto_4", "character": "赛托",
             "name": "哨戒夜巡", "release_date": "2026-01-01"},
        ]
        catalog = {
            "groups": {
                "11060105": {"Infos": [{"Name": "/assets/a.bundle", "Size": 1}]},
                "11060405": {"Infos": []},
                "11031104": {"Infos": [{"Name": "/assets/b.bundle", "Size": 1}]},
            },
            "infos": [],
            "slices": [],
            "delete_ids": [],
            "setting_ids": [],
        }
        internal = {"110601": "hameier", "110604": "coquelicot", "110311": "ceto"}
        ready, blocked = emu.select_targets(skins, catalog, internal)
        self.assertEqual([row["painting"] for row in ready], ["ceto_4"])  # 新的排前面
        self.assertEqual([row["painting"] for row in blocked], ["coquelicot_5"])
        # hameier_5 本地已有，被 only_missing 过滤
        ready_all, _ = emu.select_targets(skins, catalog, internal, only_missing=False)
        self.assertIn("hameier_5", [row["painting"] for row in ready_all])

    def test_painting_filter_and_limit(self):
        skins = [
            {"type": "live2d", "painting": f"ceto_{i}", "character": "赛托",
             "name": f"装束{i}", "release_date": f"2026-0{i}-01"}
            for i in range(3, 6)
        ]
        catalog = {
            "groups": {f"1103110{i}": {"Infos": [{"Name": "/assets/a.bundle"}]} for i in range(3, 6)},
            "infos": [], "slices": [], "delete_ids": [], "setting_ids": [],
        }
        internal = {"110311": "ceto"}
        ready, _ = emu.select_targets(skins, catalog, internal, limit=2)
        self.assertEqual(len(ready), 2)
        ready, _ = emu.select_targets(skins, catalog, internal, paintings=["ceto_4"])
        self.assertEqual([row["painting"] for row in ready], ["ceto_4"])


class RefreshIndexTests(unittest.TestCase):
    def build_catalog(self) -> dict:
        infos = [
            {"Name": "/assets/aa/aaa.bundle", "Hash": "h1", "Size": 100},
            {"Name": "/assets/bb/bbb.bundle", "Hash": "h2", "Size": 200},
        ]
        return {
            "groups": {
                "11060105": {"Infos": [{"Name": "/assets/aa/aaa.bundle", "Size": 100}]},
                "11060405": {"Infos": [{"Name": "/assets/cc/ccc.bundle", "Size": 300}]},
            },
            "infos": infos,
            "slices": [{"Filename": "res_x.slice", "FileCount": 2, "Version": 0, "FileSize": 10}],
            "delete_ids": [],
            "setting_ids": [],
        }

    def test_entries_offsets_and_complete_flags(self):
        with tempfile.TemporaryDirectory() as temp:
            index = Path(temp) / "classify_index.json"
            gmap = Path(temp) / "classify_group_map.json"
            emu.refresh_indexes(self.build_catalog(), index_path=index, map_path=gmap)
            payload = json.loads(index.read_text(encoding="utf-8"))
            self.assertEqual(payload["entries"]["/assets/aa/aaa.bundle"]["off"], 512)
            self.assertEqual(payload["entries"]["/assets/bb/bbb.bundle"]["off"], 1536)
            self.assertTrue(payload["groups"]["11060105"]["complete"])
            self.assertFalse(payload["groups"]["11060405"]["complete"])

            mapping = json.loads(gmap.read_text(encoding="utf-8"))
            self.assertEqual(mapping["11060105"]["internal"], "hameier")
            self.assertEqual(mapping["11060105"]["skin_name"], "依偎涟漪")
            self.assertFalse(mapping["11060105"]["review"])
            self.assertFalse(mapping["11060405"]["downloadable"])


class BlockedReasonTests(unittest.TestCase):
    def test_reasons(self):
        row = {"skin": {"character": "可可莉克", "name": "绯夜初醒"}, "painting": "coquelicot_5",
               "group_id": "11060405"}
        queued = {"groups": {}, "delete_ids": [], "setting_ids": ["11060405"]}
        self.assertIn("待下载列表", emu.describe_blocked(row, queued, []))
        empty = {"groups": {}, "delete_ids": [], "setting_ids": []}
        self.assertIn("无该组", emu.describe_blocked(row, empty, []))
        self.assertIn("已在设备上删除", emu.describe_blocked(row, empty, ["11060405"]))


if __name__ == "__main__":
    unittest.main()


class WatchModeTests(unittest.TestCase):
    def test_normalize_family_strips_repeated_model(self):
        self.assertEqual(emu.normalize_family("characters_ceto_4_ceto_4"), "characters_ceto_4")
        self.assertEqual(emu.normalize_family("cg_chain_2_chain_2"), "cg_chain_2")
        self.assertEqual(emu.normalize_family("characters_hameier_5"), "characters_hameier_5")

    def test_painting_from_family(self):
        self.assertEqual(emu.painting_from_family("characters_ceto_4"), "ceto_4")
        self.assertEqual(emu.painting_from_family("cg_chain_2"), "chain_2")
        self.assertEqual(emu.painting_from_family("female_2"), "female_2")

    def test_group_captured_falls_back_to_model_name(self):
        entries = [
            {"path": "a", "ab": "assets/art_live2d_characters_ceto_4_ceto_4.2048.bundle",
             "model": "", "has_moc": False},
            {"path": "b", "ab": "", "model": "Ceto_4", "has_moc": True},
            {"path": "c", "ab": "assets/build_animations2d_characters_ceto_4.bundle",
             "model": "", "has_moc": False},
        ]
        groups = emu.group_captured(entries)
        self.assertEqual(set(groups), {"characters_ceto_4"})
        self.assertEqual(len(groups["characters_ceto_4"]), 2)

    def test_probe_bundle_on_junk(self):
        self.assertEqual(emu.probe_bundle(b"not a bundle"), ("", "", False))
