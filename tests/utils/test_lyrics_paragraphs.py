"""歌词分段（`#NoHover`）与「英文行很多」的逐行配对（用户 2026-10-27 对照《binary biotope》）。

三件事：
1. 网易云给的是「一行日文、一行中文」的逐句歌词（`netease_lyrics()` 已按时间标签穿插），
   这种来源**配对与行数都确定**，只缺段落 —— 以前 `split_paired_lines()` 要求「带假名的行
   占多数」，一首大半是英文的歌（37 句里 24 句英文）就被判成「认不出」，整段丢给模型，
   结果反而丢了分段；
2. `(light falling）` 这种「英文 + 全角括号」的行以前算成中文，译文跟它并进同一行；
3. 段落只能按语义判断（时间戳的间隔判不出来），所以「AI 识别并填入」会多要一次
   「在第几行之后空一行」，只动空行、不动字。
"""
import json
from types import SimpleNamespace
from unittest import TestCase
from unittest import mock

from utils import ai_lyrics, lyrics_editor

SETTINGS = {"provider": "openai", "base_url": "https://api.example.com/v1",
            "model": "test-model", "api_key": "sk-test", "thinking": False}

# 真实来源的一小段（网易云 1928643076）：日文 / 英文混排，逐行配好对
PAIRED = "\n".join([
    "冬を忍ぶままに　心　鍵を掛けて",
    "将冬日悄然藏匿 心门紧锁封闭",
    "揺らぐ水面は凍りついた",
    "摇曳水面已凝结成冰",
    "壊れた細胞は　氷解したとしても",
    "纵使破碎细胞 尽数消融冰释",
    "二度と同じ形には　戻らないから",
    "也无法再度 恢复往昔形姿",
    "All I need is your voice",
    "我只需要你的声音",
    "Why do they laugh at contracts as dependence?",
    "为何他们将契约讥讽为依附？",
    "Even though there is no such thing as Agape",
    "明明世间根本不存在圣洁之爱",
    "(I'm falling)",
    "（我正在坠落）",
    "The scars will never heal",
    "伤痕永不愈合",
    "(light falling）",
    "（光芒坠落）",
])


def _config(allowed=True):
    return SimpleNamespace(wikitext=SimpleNamespace(ai_lyrics=allowed, furigana_all=False))


def _reply(payload):
    return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}


class SplitPairedLinesTest(TestCase):
    def test_mostly_english_pairs_are_still_recognised(self):
        """37 句里 24 句英文的配对（《binary biotope》）也算逐行配对。"""
        pairs = lyrics_editor.split_paired_lines(PAIRED)
        self.assertIsNotNone(pairs)
        jap, chs = pairs
        self.assertEqual(10, len(jap.split("\n")))
        self.assertIn("All I need is your voice", jap)
        self.assertNotIn("All I need is your voice", chs)

    def test_chinese_first_order_is_rejected(self):
        """中文在前、日文在后时不能误判（假名行会落在中文格子里）。"""
        lines = PAIRED.split("\n")
        swapped = []
        for index in range(0, len(lines), 2):
            swapped.extend((lines[index + 1], lines[index]))
        self.assertIsNone(lyrics_editor.split_paired_lines("\n".join(swapped)))

    def test_english_only_column_without_translation_is_rejected(self):
        """右边不是译文（也是英文）时不是这个形状。"""
        text = "\n".join(["All I need is your voice", "just english here"] * 5)
        self.assertIsNone(lyrics_editor.split_paired_lines(text))


class EnglishLineWithFullWidthPunctuationTest(TestCase):
    def test_full_width_bracket_line_counts_as_english(self):
        self.assertTrue(lyrics_editor.is_english_line("(light falling）"))

    def test_stanza_keeps_it_in_the_japanese_column(self):
        """落在日语格子上的英文行归日语栏（不然中文栏会把它的译文并进上一行）。"""
        stanza = ["冬を忍ぶままに　心　鍵を掛けて",
                  "将冬日悄然藏匿 心门紧锁封闭",
                  "揺らぐ水面は凍りついた",
                  "摇曳水面已凝结成冰",
                  "(light falling）",
                  "（光芒坠落）",
                  "I don't need your pity",
                  "我不需要你的怜悯"]
        jap, chs, roma = lyrics_editor.classify_stanza(stanza)
        self.assertEqual([], roma)
        self.assertIn("(light falling）", jap)
        self.assertEqual(["将冬日悄然藏匿 心门紧锁封闭",
                          "摇曳水面已凝结成冰",
                          "（光芒坠落）",
                          "我不需要你的怜悯"], chs)
        self.assertEqual(len(jap), len(chs))


class ArrangeParagraphsTest(TestCase):
    LINES_JAP = ["a1", "a2", "a3", "a4"]
    LINES_CHS = ["b1", "b2", "b3", "b4"]

    def _arrange(self, reply, allowed=True, settings=None):
        with mock.patch.object(ai_lyrics, "get_config", return_value=_config(allowed)), \
             mock.patch.object(ai_lyrics.ai_css, "settings",
                               return_value=settings or SETTINGS), \
             mock.patch.object(ai_lyrics.ai_css, "_post",
                               return_value=(reply, "")) as post:
            return (ai_lyrics.arrange_paragraphs("\n".join(self.LINES_JAP),
                                                 "\n".join(self.LINES_CHS)), post)

    def test_blank_line_goes_after_the_given_line(self):
        result, _ = self._arrange(_reply({"breaks": [2]}))
        self.assertTrue(result["ok"])
        self.assertEqual("a1\na2\n\na3\na4", result["jap"])
        self.assertEqual("b1\nb2\n\nb3\nb4", result["chs"])
        self.assertEqual([2], result["breaks"])
        self.assertEqual("test-model", result["model"])

    def test_junk_breaks_are_dropped(self):
        result, _ = self._arrange(_reply({"breaks": [0, 4, 2, 2, "x", 1]}))
        # 0 / 4（最后一行之后）/ "x" 都不要，2 重复只留一个，结果递增
        self.assertEqual([1, 2], result["breaks"])

    def test_no_usable_breaks_is_an_error(self):
        result, _ = self._arrange(_reply({"breaks": [9]}))
        self.assertFalse(result["ok"])
        self.assertIn("分段", result["error"])

    def test_reply_must_be_json(self):
        result, _ = self._arrange({"choices": [{"message": {"content": "好的！"}}]})
        self.assertFalse(result["ok"])

    def test_network_error(self):
        with mock.patch.object(ai_lyrics, "get_config", return_value=_config(True)), \
             mock.patch.object(ai_lyrics.ai_css, "settings", return_value=SETTINGS), \
             mock.patch.object(ai_lyrics.ai_css, "_post", return_value=(None, "boom")):
            result = ai_lyrics.arrange_paragraphs("\n".join(self.LINES_JAP),
                                                  "\n".join(self.LINES_CHS))
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["error"])

    def test_switch_off_never_calls_api(self):
        result, post = self._arrange(_reply({"breaks": [2]}), allowed=False)
        self.assertFalse(result["ok"])
        self.assertIn("ai_lyrics", result["error"])
        self.assertFalse(post.called)

    def test_lines_must_match_and_be_unbroken(self):
        with mock.patch.object(ai_lyrics, "get_config", return_value=_config(True)), \
             mock.patch.object(ai_lyrics.ai_css, "settings", return_value=SETTINGS), \
             mock.patch.object(ai_lyrics.ai_css, "_post") as post:
            self.assertFalse(ai_lyrics.arrange_paragraphs("a1\na2", "b1")["ok"])
            self.assertFalse(ai_lyrics.arrange_paragraphs("a1\n\na2", "b1\n\nb2")["ok"])
        self.assertFalse(post.called)

    def test_prompt_lists_every_pair_with_its_number(self):
        prompt = ai_lyrics.build_paragraph_prompt(self.LINES_JAP, self.LINES_CHS)
        self.assertIn("1 | a1 | b1", prompt)
        self.assertIn("4 | a4 | b4", prompt)
        self.assertIn('{"breaks"', prompt)


class AiAutoParagraphTest(TestCase):
    """「AI 识别并填入」走配对捷径时，补分段这一趟失败不影响原来的结果。"""

    def _ai_auto(self, arrange):
        api = lyrics_editor.LyricsApi()
        with mock.patch.object(ai_lyrics, "arrange_paragraphs", **arrange):
            return api.ai_auto(json.dumps({"text": PAIRED, "jap": ""}, ensure_ascii=False))

    def test_paragraphs_from_the_model_reach_both_columns(self):
        result = self._ai_auto({"return_value": {
            "ok": True, "model": "test-model", "breaks": [4, 7],
            "jap": "冬を忍ぶままに　心　鍵を掛けて\n揺らぐ水面は凍りついた\n壊れた細胞は　氷解したとしても\n"
                   "二度と同じ形には　戻らないから\n\nAll I need is your voice\n"
                   "Why do they laugh at contracts as dependence?\n"
                   "Even though there is no such thing as Agape\n\n(I'm falling)\n"
                   "The scars will never heal\n(light falling）",
            "chs": "将冬日悄然藏匿 心门紧锁封闭\n摇曳水面已凝结成冰\n纵使破碎细胞 尽数消融冰释\n"
                   "也无法再度 恢复往昔形姿\n\n我只需要你的声音\n为何他们将契约讥讽为依附？\n"
                   "明明世间根本不存在圣洁之爱\n\n（我正在坠落）\n伤痕永不愈合\n（光芒坠落）"}})
        self.assertTrue(result["ok"])
        self.assertEqual("paired", result["mode"])
        self.assertEqual("", result["roma"])
        self.assertIn("\n\n", result["jap"])
        self.assertIn("\n\n", result["chs"])
        self.assertNotIn("(light falling）\n", result["chs"])
        self.assertEqual(result["jap"].count("\n\n"), result["chs"].count("\n\n"))
        self.assertIn("分段由 AI 判断", result["message"])

    def test_failure_keeps_the_plain_paired_result(self):
        result = self._ai_auto({"return_value": {"ok": False, "error": "未配置 ai_api_key"}})
        self.assertTrue(result["ok"])
        self.assertEqual("paired", result["mode"])
        self.assertNotIn("\n\n", result["jap"])
        self.assertNotIn("分段由 AI", result["message"])
        self.assertEqual(10, len(result["jap"].split("\n")))

    def test_existing_paragraphs_are_left_alone(self):
        text = PAIRED.replace("All I need is your voice", "\nAll I need is your voice", 1)
        api = lyrics_editor.LyricsApi()
        with mock.patch.object(ai_lyrics, "arrange_paragraphs") as arrange:
            api.ai_auto(json.dumps({"text": text, "jap": ""}, ensure_ascii=False))
        self.assertFalse(arrange.called)
