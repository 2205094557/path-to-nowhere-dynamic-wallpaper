from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tools"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sync_voice_lines import categorize, parse_voice_entries


class ParseVoiceEntriesTests(unittest.TestCase):
    def test_parses_plain_entries(self) -> None:
        wikitext = "{{语音|主体=雷温|动作=问候|语音文字=今天的你……}}"
        self.assertEqual(
            parse_voice_entries(wikitext),
            [{"subject": "雷温", "action": "问候", "text": "今天的你……"}],
        )

    def test_strips_html_comments_from_params(self) -> None:
        # BWiki 模板占位注释：落在主体里会让音频文件名拼错，落在文本里会污染台词
        wikitext = "{{语音|主体=唐<!--禁闭者姓名-->|动作=问候|语音文字=<!--填写语音文字-->你好}}"
        self.assertEqual(
            parse_voice_entries(wikitext),
            [{"subject": "唐", "action": "问候", "text": "你好"}],
        )

    def test_strips_inline_comment_inside_text(self) -> None:
        wikitext = "{{语音|主体=夏音|动作=交谈1|语音文字=这里和中央<!-- 语音是：环 -->研究院比，简直就是天堂！}}"
        entries = parse_voice_entries(wikitext)
        self.assertEqual(entries[0]["text"], "这里和中央研究院比，简直就是天堂！")

    def test_ignores_entries_without_action_or_text(self) -> None:
        wikitext = "{{语音|主体=雷温|动作=问候}}{{语音|主体=雷温|语音文字=无动作}}"
        self.assertEqual(parse_voice_entries(wikitext), [])

    def test_categorize_prefers_exact_prefixes(self) -> None:
        self.assertEqual(categorize("普通触摸3"), "touch")
        self.assertEqual(categorize("获得装束"), "greet")
        self.assertEqual(categorize("闲置-女"), "idle")
        # 四星皮二阶段互动台词
        self.assertEqual(categorize("互动1"), "interact")
        self.assertEqual(categorize("互动5"), "interact")
        self.assertEqual(categorize("退出互动"), "interact_exit")
        self.assertIsNone(categorize("关卡开始"))


if __name__ == "__main__":
    unittest.main()
