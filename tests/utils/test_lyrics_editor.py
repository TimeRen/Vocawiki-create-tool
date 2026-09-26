"""utils/lyrics_editor.py 的单元测试：分类 / 切分 / pywebview 接口。"""
import json
from unittest import TestCase
from unittest import mock

from utils import lyrics_editor
from utils.lyrics_editor import (LyricsApi, classify_by_script, classify_stanza,
                                 extract_chs_by_jap, guess_layout, normalize_blank_lines,
                                 process_lyrics_jap, process_translation)


class ProcessLyricsJapTest(TestCase):
    """日语歌词预处理：换行过多时重新分段（原在 utils/string.py，现并入本模块）。"""

    SONG = """翼広げ、どこか遠く、

ひとりという名の鳥になり飛んで

いけたらいいな、そこには見たことない

きれいなものがあるの"""

    def test_keeps_normal_paragraphs(self):
        self.assertEqual("\n".join(self.SONG.split("\n\n")), process_lyrics_jap(self.SONG))

    def test_collapses_excessive_newlines(self):
        text = "\n\n\n\n\n\n".join(["ABC\n\n\nDEF\n\n\n\nGHI\n\n\n\nJKL" for _ in range(3)])
        expected = "\n\n".join(["ABC\nDEF\nGHI\nJKL" for _ in range(3)])
        self.assertEqual(expected, process_lyrics_jap(text))

    def test_drops_carriage_returns(self):
        self.assertEqual("a\nb", process_lyrics_jap("a\r\nb"))

    def test_empty_returns_empty(self):
        self.assertEqual("", process_lyrics_jap(""))
        self.assertEqual("", process_lyrics_jap("   "))


class NormalizeBlankLinesTest(TestCase):
    """段落之间的连续空行只留一个（否则自动识别出来的三栏里空行会连成一片）。"""

    def test_collapses_run_to_single_blank_line(self):
        self.assertEqual("A\nB\n\nC", normalize_blank_lines("A\nB\n\n\n\nC"))

    def test_single_blank_line_is_kept(self):
        self.assertEqual("A\n\nB", normalize_blank_lines("A\n\nB"))

    def test_blank_line_with_spaces_counts(self):
        self.assertEqual("A\n\nB", normalize_blank_lines("A\n  \n\t\nB"))

    def test_carriage_returns_are_normalized(self):
        self.assertEqual("A\n\nB", normalize_blank_lines("A\r\n\r\n\r\nB"))

    def test_lines_are_not_merged(self):
        self.assertEqual("A\nB\nC", normalize_blank_lines("A\nB\nC"))

    def test_empty(self):
        self.assertEqual("", normalize_blank_lines(""))
        self.assertEqual("", normalize_blank_lines(None))


class ProcessTranslationTest(TestCase):
    def test_takes_requested_line_of_each_group(self):
        text = "日1\n中1\n日2\n中2"
        self.assertEqual("日1\n日2", process_translation(text, 2, 1))
        self.assertEqual("中1\n中2", process_translation(text, 2, 2))

    def test_collapses_paragraph_separator(self):
        # 段间的两个空行：组首落在空行上时输出一个空行作为段落分隔
        text = "日1\n中1\n\n\n日2\n中2"
        self.assertEqual("日1\n\n日2", process_translation(text, 3, 1))

    def test_keeps_blank_lines_as_separators(self):
        # 空行落在组首时保留为空行；落在组内其他位置则不影响
        self.assertEqual("日1\n日2", process_translation("日1\n中1\n\n日2\n中2", 3, 1))

    def test_drops_group_missing_target_line(self):
        # 末尾不足一组（少了中文行）时，中文的那一路直接丢掉
        self.assertEqual("中1", process_translation("日1\n中1\n日2", 2, 2))

    def test_leading_blank_line_does_not_crash(self):
        # 旧实现会在 result 为空时访问 result[-1]，这里应安全返回
        self.assertEqual("", process_translation("\n\n日1", 2, 2))

    def test_empty_text(self):
        self.assertEqual("", process_translation("", 3, 1))


class ClassifyStanzaTest(TestCase):
    def test_kana_is_japanese(self):
        jap, chs, roma = classify_stanza(["きょうの", "今日的"])
        self.assertEqual(["きょうの"], jap)
        self.assertEqual(["今日的"], chs)
        self.assertEqual([], roma)

    def test_ascii_is_romaji(self):
        jap, chs, roma = classify_stanza(["君の名は", "kimi no na wa"])
        self.assertEqual(["君の名は"], jap)
        self.assertEqual([], chs)
        self.assertEqual(["kimi no na wa"], roma)

    def test_alternating_block(self):
        # 日语 / 中文 交替
        stanza = ["きみの", "你的", "ぼくの", "我的"]
        jap, chs, roma = classify_stanza(stanza)
        self.assertEqual(["きみの", "ぼくの"], jap)
        self.assertEqual(["你的", "我的"], chs)

    def test_alternating_block_with_romaji(self):
        # 日语 / 罗马音 / 中文 三行一组
        stanza = ["きみの", "kimi no", "你的", "ぼくの", "boku no", "我的"]
        jap, chs, roma = classify_stanza(stanza)
        self.assertEqual(["きみの", "ぼくの"], jap)
        self.assertEqual(["你的", "我的"], chs)
        self.assertEqual(["kimi no", "boku no"], roma)

    def test_block_format_japanese_first(self):
        # 块状：日语在上、纯汉字译文在下（无假名，需按块判定）
        stanza = ["君の名前", "僕の名前", "你的名字", "我的名字"]
        jap, chs, roma = classify_stanza(stanza)
        self.assertEqual(["君の名前", "僕の名前"], jap)
        self.assertEqual(["你的名字", "我的名字"], chs)

    def test_punctuation_only_lines_are_dropped(self):
        jap, chs, roma = classify_stanza(["きみの", "——", "你的"])
        self.assertEqual(["きみの"], jap)
        self.assertEqual(["你的"], chs)

    def test_empty_stanza(self):
        self.assertEqual(([], [], []), classify_stanza([]))


class ClassifyByScriptTest(TestCase):
    def test_classifies_full_text(self):
        text = "きみの\n你的\n\nぼくの\n我的"
        jap, chs, roma = classify_by_script(text)
        self.assertEqual("きみの\n\nぼくの", jap)
        self.assertEqual("你的\n\n我的", chs)
        self.assertEqual("", roma)

    def test_returns_empty_strings_when_unknown(self):
        self.assertEqual(("", "", ""), classify_by_script("？？？\n！！！"))

    def test_blank_runs_collapse_to_one_line(self):
        # 待归类歌词里段落间有 3 个空行时，三栏里只应留 1 个
        text = "きみの\n你的\n\n\n\nぼくの\n我的"
        jap, chs, roma = classify_by_script(text)
        self.assertEqual("きみの\n\nぼくの", jap)
        self.assertEqual("你的\n\n我的", chs)
        self.assertEqual("", roma)


class ExtractChsByJapTest(TestCase):
    def test_keeps_lines_not_in_japanese(self):
        text = "きみの\n你的\nぼくの\n我的"
        self.assertEqual("你的\n我的", extract_chs_by_jap(text, "きみの\nぼくの"))

    def test_keeps_blank_lines(self):
        self.assertEqual("你的\n\n我的", extract_chs_by_jap("きみの\n你的\n\nぼくの\n我的", "きみの\nぼくの"))

    def test_blank_runs_collapse_to_one_line(self):
        self.assertEqual("你的\n\n我的",
                         extract_chs_by_jap("きみの\n你的\n\n\nぼくの\n我的", "きみの\nぼくの"))

    def test_empty_japanese_returns_empty(self):
        self.assertEqual("", extract_chs_by_jap("きみの\n你的", ""))
        self.assertEqual("", extract_chs_by_jap("きみの\n你的", "   "))


class GuessLayoutTest(TestCase):
    def test_guesses_group_length_and_line_numbers(self):
        text = ("きみの\n你的\n\n" * 3).strip()
        layout = guess_layout(text)
        self.assertIsNotNone(layout)
        self.assertEqual("3", layout["group_length"])
        self.assertEqual("1", layout["jap_line"])
        self.assertEqual("2", layout["chs_line"])
        self.assertEqual("", layout["roma_line"])

    def test_detects_romaji_line(self):
        text = ("きみの\nkimi no\n你的\n\n" * 3).strip()
        layout = guess_layout(text)
        self.assertIsNotNone(layout)
        self.assertEqual("2", layout["roma_line"])

    def test_single_group_without_blank_lines(self):
        # 没有分隔空行时按「每组两行」处理（与原 tkinter 版行为一致）
        layout = guess_layout("きみの\n你的")
        self.assertEqual("2", layout["group_length"])
        self.assertEqual("1", layout["jap_line"])
        self.assertEqual("2", layout["chs_line"])

    def test_returns_none_for_single_line(self):
        self.assertIsNone(guess_layout("きみの"))
        self.assertIsNone(guess_layout(""))


class LyricsApiTest(TestCase):
    def setUp(self):
        self.api = LyricsApi()
        self.window = mock.Mock()
        self.api._window = self.window

    def _call(self, method, **payload):
        return method(json.dumps(payload))

    # —— auto ——

    def test_auto_requires_text(self):
        result = self._call(self.api.auto, text="  ")
        self.assertFalse(result["ok"])
        self.assertIn("粘贴歌词", result["error"])

    def test_auto_bad_json(self):
        self.assertFalse(self.api.auto("{oops")["ok"])

    def test_auto_prefers_extracting_with_existing_japanese(self):
        result = self._call(self.api.auto, text="きみの\n你的", jap="きみの")
        self.assertTrue(result["ok"])
        self.assertEqual("extract", result["mode"])
        self.assertEqual("你的", result["chs"])

    def test_auto_classifies_by_script(self):
        result = self._call(self.api.auto, text="きみの\n你的")
        self.assertEqual("classify", result["mode"])
        self.assertEqual("きみの", result["jap"])
        self.assertEqual("你的", result["chs"])

    def test_auto_falls_back_to_guessing_lines(self):
        # 纯标点（既非假名、非 ASCII、非汉字）时脚本分类认不出，改按重复段结构猜
        text = "？？\n。，\n\n？？\n。，"
        self.assertEqual(("", "", ""), classify_by_script(text))
        result = self._call(self.api.auto, text=text)
        self.assertTrue(result["ok"])
        self.assertEqual("lines", result["mode"])
        self.assertEqual("3", result["layout"]["group_length"])

    def test_auto_reports_failure(self):
        result = self._call(self.api.auto, text="？？？")
        self.assertFalse(result["ok"])
        self.assertIn("失败", result["error"])

    def test_auto_collapses_blank_runs(self):
        # 点「自动识别」后段落之间不能留 2 个以上空行
        result = self._call(self.api.auto, text="きみの\n你的\n\n\n\nぼくの\n我的")
        self.assertTrue(result["ok"])
        self.assertEqual("きみの\n\nぼくの", result["jap"])
        self.assertEqual("你的\n\n我的", result["chs"])

    def test_auto_normalizes_existing_columns(self):
        # 日语栏自己写的 2 个空行也一起压掉
        result = self._call(self.api.auto, text="きみの\n你的\n\n\nぼくの",
                            jap="きみの\n\n\nぼくの", roma="a\n\n\nb")
        self.assertEqual("extract", result["mode"])
        self.assertEqual("きみの\n\nぼくの", result["jap"])
        self.assertEqual("a\n\nb", result["roma"])

    # —— 从来源链接填充 ——
    def test_fill_source_delegates(self):
        filled = {"ok": True, "translator": "白夜落星", "sourceName": "网易云音乐"}
        with mock.patch.object(lyrics_editor.source_filler, "fill_source",
                               return_value=filled) as filler:
            self.assertEqual(filled, self.api.fill_source("https://music.163.com/#/song?id=1"))
        filler.assert_called_once_with("https://music.163.com/#/song?id=1")

    # —— 演唱者上色（{{LyricsKai/colors}}）——

    def test_context_carries_colors_and_charas(self):
        api = LyricsApi(charas=["宮舞モカ", "Ryo"], use_colors=True)
        with mock.patch.object(lyrics_editor.lyrics_colors, "fetch_colors",
                               return_value={"宫舞茉歌": "#72A6C0", "RYO": "#EB6238"}):
            context = api.get_context()
        self.assertTrue(context["useColors"])
        self.assertEqual([{"name": "宮舞モカ", "color": "#333333"},
                          {"name": "Ryo", "color": "#EB6238"}], context["charas"])

    def test_context_without_charas(self):
        self.assertEqual([], LyricsApi().get_context()["charas"])

    def test_save_records_chara_marks(self):
        self._call(self.api.save, jap="a\nb", chs="啊\n哦", useColors=True,
                   charaMarks={"0": ["A"], "1": ["A", "B"], "2": []})
        self.assertTrue(self.api.result.use_colors)
        self.assertEqual({"0": ["A"], "1": ["A", "B"]}, self.api.result.chara_marks)

    def test_save_records_inline_splits(self):
        self._call(self.api.save, jap="未来は誰も知らない", chs="未來無人知曉", useColors=True,
                   charaMarks={"0": [["A"], ["B"]]},
                   charaSplits={"0": {"jap": [2], "chs": [2], "roma": []}, "1": {}})
        self.assertEqual({"0": [["A"], ["B"]]}, self.api.result.chara_marks)
        self.assertEqual({"0": {"jap": [2], "chs": [2]}}, self.api.result.chara_splits)

    def test_save_without_splits(self):
        self._call(self.api.save, jap="a", chs="啊")
        self.assertIsNone(self.api.result.chara_splits)

    def test_save_without_marks(self):
        self._call(self.api.save, jap="a", chs="啊")
        self.assertFalse(self.api.result.use_colors)
        self.assertIsNone(self.api.result.chara_marks)

    # —— convert ——

    def test_convert_splits_by_line_numbers(self):
        result = self._call(self.api.convert, text="日1\n中1\n日2\n中2",
                            groupLength="2", japLine="1", chsLine="2", romaLine="")
        self.assertTrue(result["ok"])
        self.assertEqual("日1\n日2", result["jap"])
        self.assertEqual("中1\n中2", result["chs"])
        self.assertEqual("", result["roma"])

    def test_convert_requires_group_length(self):
        result = self._call(self.api.convert, text="日1\n中1", groupLength="")
        self.assertFalse(result["ok"])
        self.assertIn("整数", result["error"])

    def test_convert_requires_text(self):
        self.assertFalse(self._call(self.api.convert, text="", groupLength="3")["ok"])

    # —— save / cancel ——

    def test_save_builds_lyrics_and_closes_window(self):
        result = self._call(self.api.save, jap="きみの", chs="你的", roma="kimi no",
                            translator="某人", translatorUrl="https://x", sourceName="AtWiki",
                            sourceUrl="https://y")
        self.assertTrue(result["ok"])
        lyrics = self.api.result
        self.assertEqual("きみの", lyrics.lyrics_jap)
        self.assertEqual("你的", lyrics.lyrics_chs)
        self.assertEqual("kimi no", lyrics.lyrics_roma)
        self.assertEqual("某人", lyrics.translator)
        self.assertEqual("AtWiki", lyrics.source_name)
        self.window.destroy.assert_called_once()

    def test_save_requires_some_lyrics(self):
        result = self._call(self.api.save, jap="", chs="  ", roma="kimi")
        self.assertFalse(result["ok"])
        self.assertIn("都是空的", result["error"])
        self.assertIsNone(self.api.result)

    def test_cancel_leaves_no_result(self):
        self.assertTrue(self.api.cancel()["ok"])
        self.assertIsNone(self.api.result)
        self.window.destroy.assert_called_once()

    # —— 使用 LyricsKai/hover 开关 ——

    def test_save_records_hover_switch(self):
        self._call(self.api.save, jap="きみの", chs="你的", useHover=True)
        self.assertTrue(self.api.result.use_hover)

    def test_save_defaults_hover_to_off(self):
        self._call(self.api.save, jap="きみの", chs="你的")
        self.assertFalse(self.api.result.use_hover)

    def test_get_context(self):
        api = LyricsApi("预填歌词", "AtWiki", use_hover=True)
        with mock.patch.object(lyrics_editor.ai_lyrics, "context",
                               return_value={"enabled": True, "hidden": False}):
            context = api.get_context()
        self.assertEqual({"initial": "预填歌词", "sourceHint": "AtWiki", "useHover": True,
                          "useColors": False, "charas": [],
                          "aiLyrics": {"enabled": True, "hidden": False}},
                         context)

    def test_ai_auto_delegates_to_ai_lyrics(self):
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize",
                               return_value={"ok": True, "jap": "あ"}) as recognize:
            self.assertEqual({"ok": True, "jap": "あ"}, self.api.ai_auto('{"text": "あ"}'))
        recognize.assert_called_once_with('{"text": "あ"}')


class OpenEditorTest(TestCase):
    """歌词整理入口：交给 GUI 门面（界面已是主窗口里的「歌词」页）。"""

    def test_returns_none_without_gui(self):
        from utils import lyrics_editor, ui
        with mock.patch.object(ui, "is_active", return_value=False):
            self.assertIsNone(lyrics_editor.open_lyrics_editor())

    def test_delegates_to_gui_facade(self):
        from models.song import Lyrics
        from utils import lyrics_editor, ui
        lyrics = Lyrics(lyrics_jap="きみの", lyrics_chs="你的", use_hover=True)
        with mock.patch.object(ui, "is_active", return_value=True), \
             mock.patch.object(ui, "open_lyrics_editor", return_value=lyrics) as open_editor:
            result = lyrics_editor.open_lyrics_editor("预填", "手动粘贴", True, False, ["初音未来"])
        self.assertIs(result, lyrics)
        open_editor.assert_called_once_with("预填", "手动粘贴", True, False, ["初音未来"])

    def test_returns_none_when_cancelled(self):
        from utils import lyrics_editor, ui
        with mock.patch.object(ui, "is_active", return_value=True), \
             mock.patch.object(ui, "open_lyrics_editor", return_value=None):
            self.assertIsNone(lyrics_editor.open_lyrics_editor())


class SongWiringTest(TestCase):
    def test_get_manual_lyrics_delegates_to_editor(self):
        import models.song
        with mock.patch.object(models.song, "open_lyrics_editor",
                               return_value=mock.Mock(lyrics_jap="きみの")) as editor:
            lyrics = models.song.get_manual_lyrics("预填", use_hover=True)
        self.assertEqual("きみの", lyrics.lyrics_jap)
        editor.assert_called_once_with("预填", use_hover=True, use_colors=False, charas=())

    def test_get_manual_lyrics_defaults_hover_off(self):
        import models.song
        with mock.patch.object(models.song, "open_lyrics_editor",
                               return_value=mock.Mock(lyrics_jap="きみの")) as editor:
            models.song.get_manual_lyrics("预填")
        editor.assert_called_once_with("预填", use_hover=False, use_colors=False, charas=())

    def test_get_manual_lyrics_returns_empty_on_cancel(self):
        import models.song
        with mock.patch.object(models.song, "open_lyrics_editor", return_value=None):
            lyrics = models.song.get_manual_lyrics()
        self.assertIsNone(lyrics.lyrics_jap)
        self.assertIsNone(lyrics.lyrics_chs)
