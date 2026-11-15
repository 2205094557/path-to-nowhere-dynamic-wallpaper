from __future__ import annotations

import io
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tools"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bwiki_lib.client import PolicyError, ResponseTooLarge, WikiClient, read_limited, validate_url
from bwiki_lib.images import ImageValidationError, inspect_image, safe_component
from bwiki_lib.io_utils import atomic_write_json
from bwiki_lib.parser import attach_imageinfo, parse_character_cards, parse_characters, parse_skin_detail, parse_skins

FIXTURES = Path(__file__).parent / "fixtures"


class FakeHeaders(dict):
    def get_content_type(self):
        return self.get("Content-Type", "application/octet-stream").split(";", 1)[0]


class FakeResponse:
    def __init__(self, url: str, data: bytes, status: int = 200, headers: dict | None = None):
        self._url = url
        self._stream = io.BytesIO(data)
        self.status = status
        self.headers = FakeHeaders(headers or {"Content-Type": "application/json"})

    def read(self, count=-1):
        return self._stream.read(count)

    def geturl(self):
        return self._url

    def getcode(self):
        return self.status

    def close(self):
        pass


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return self.responses.pop(0)


class ClientTests(unittest.TestCase):
    def test_url_policy(self):
        self.assertEqual(validate_url("https://wiki.biligame.com/wqmt/api.php"), "https://wiki.biligame.com/wqmt/api.php")
        self.assertEqual(validate_url("https://patchwiki.biligame.com/x.png"), "https://patchwiki.biligame.com/x.png")
        for url in ("http://wiki.biligame.com/x", "https://evil.example/x", "https://wiki.biligame.com:444/x", "https://evil.example@wiki.biligame.com:444/x"):
            with self.subTest(url=url), self.assertRaises(PolicyError):
                validate_url(url)

    def test_action_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            client = WikiClient(Path(temp), opener=FakeOpener([]), min_interval=0)
            with self.assertRaises(PolicyError):
                client.api_get({"action": "edit"}, response_name="bad")

    def test_redirect_final_host_checked(self):
        response = FakeResponse("https://evil.example/payload", b"{}")
        with tempfile.TemporaryDirectory() as temp:
            client = WikiClient(Path(temp), opener=FakeOpener([response]), min_interval=0)
            with self.assertRaises(PolicyError):
                client.fetch("https://wiki.biligame.com/wqmt/api.php")

    def test_response_limit(self):
        with self.assertRaises(ResponseTooLarge):
            read_limited(io.BytesIO(b"12345"), 4)

    def test_get_only(self):
        response = FakeResponse("https://wiki.biligame.com/wqmt/api.php?action=query", b"{}")
        opener = FakeOpener([response])
        with tempfile.TemporaryDirectory() as temp:
            client = WikiClient(Path(temp), opener=opener, min_interval=0)
            client.fetch("https://wiki.biligame.com/wqmt/api.php?action=query")
        self.assertEqual(opener.requests[0][0].method, "GET")


class ParserTests(unittest.TestCase):
    def test_character_table(self):
        source = (FIXTURES / "characters.html").read_text(encoding="utf-8")
        items, issues = parse_characters(source, "禁闭者图鉴")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["name"], "海拉")
        self.assertEqual(items[0]["mbcc_id"], "MBCC-S-098")
        self.assertEqual(items[0]["image"]["file_title"], "文件:海拉.png")
        self.assertFalse(any(item["type"] == "no_character_table_recognized" for item in issues))

    def test_skin_table_and_placeholder(self):
        source = (FIXTURES / "skins.html").read_text(encoding="utf-8")
        items, issues = parse_skins(source, "装束图鉴")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["character"], "海拉")
        self.assertEqual(items[0]["name"], "普通装束")
        self.assertTrue(items[1]["metadata_only"])
        self.assertTrue(items[1]["upload_placeholder"])
        self.assertFalse(any(item["type"] == "no_skin_table_recognized" for item in issues))

    def test_imageinfo_attachment(self):
        payload = json.loads((FIXTURES / "imageinfo.json").read_text(encoding="utf-8"))
        info = {page["title"]: page["imageinfo"][0] for page in payload["query"]["pages"]}
        items = [{"image": {"file_title": "文件:海拉.png"}}]
        attach_imageinfo(items, info)
        self.assertEqual(items[0]["image"]["original"]["width"], 2048)

    def test_live_character_cards_deduplicate(self):
        source = """
        <span class="tab-panel">启迪</span>
        <div class="txd"><a href="/wqmt/禁闭者:赫卡蒂"><img alt="https://patchwiki.biligame.com/images/wqmt/a.png" src="https://patchwiki.biligame.com/lazy.png"></a><div class="txnum">MBCC-S-019</div><div class="CareerIcon"><img alt="启迪tjicon.png"></div><div class="txl"><a href="/wqmt/禁闭者:赫卡蒂">赫卡蒂</a></div></div>
        <div class="txd"><a href="/wqmt/禁闭者:赫卡蒂"><img alt="https://patchwiki.biligame.com/images/wqmt/a.png" src="https://patchwiki.biligame.com/lazy.png"></a><div class="txnum">MBCC-S-019</div><div class="CareerIcon"><img alt="启迪tjicon.png"></div><div class="txl"><a href="/wqmt/禁闭者:赫卡蒂">赫卡蒂</a></div></div>
        """
        items, _ = parse_character_cards(source, "首页")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["profession"], "启迪")
        self.assertEqual(items[0]["image"]["current_url"], "https://patchwiki.biligame.com/images/wqmt/a.png")

    def test_skin_detail_fields(self):
        source = "<table><tr><th>资料</th></tr><tr><td>服装介绍。</td></tr><tr><th>装束影像</th></tr><tr><td><a href='https://www.bilibili.com/video/BV1xx'>影像</a></td></tr></table><table><tr><th>问候</th><td>早上好。</td></tr></table>"
        detail = parse_skin_detail(source)
        self.assertEqual(detail["introduction"], "服装介绍。")
        self.assertEqual(detail["outfit_video"]["url"], "https://www.bilibili.com/video/BV1xx")
        self.assertEqual(detail["dialogue_fields"]["问候"], "早上好。")


class ImageTests(unittest.TestCase):
    def test_png_magic_dimensions_alpha(self):
        png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", 800, 1200, 8, 6, 0, 0, 0) + b"\0\0\0\0"
        result = inspect_image(png)
        self.assertEqual((result["width"], result["height"]), (800, 1200))
        self.assertTrue(result["png_alpha"])

    def test_reject_unknown_magic(self):
        with self.assertRaises(ImageValidationError):
            inspect_image(b"MZnot-an-image")

    def test_safe_component(self):
        self.assertEqual(safe_component("normal-01"), "normal-01")
        with self.assertRaises(ValueError):
            safe_component("..")


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_json(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "nested" / "value.json"
            atomic_write_json(path, {"value": "测试"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"value": "测试"})
            self.assertEqual(list(path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
