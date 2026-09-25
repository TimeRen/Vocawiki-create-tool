"""utils/lyrics_editor.py 的单元测试：分类 / 切分 / pywebview 接口。"""
import json
from unittest import TestCase
from unittest import mock

from utils import lyrics_editor
from utils.lyrics_editor import (LyricsApi, classify_by_script, classify_stanza,
                                 extract_chs_by_jap, guess_layout, process_translation)


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


class ExtractChsByJapTest(TestCase):
    def test_keeps_lines_not_in_japanese(self):
        text = "きみの\n你的\nぼくの\n我的"
        self.assertEqual("你的\n我的", extract_chs_by_jap(text, "きみの\nぼくの"))

    def test_keeps_blank_lines(self):
        self.assertEqual("你的\n\n我的", extract_chs_by_jap("きみの\n你的\n\nぼくの\n我的", "きみの\nぼくの"))

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
        self.assertEqual({"initial": "预填歌词", "sourceHint": "AtWiki", "useHover": True},
                         api.get_context())


class OpenEditorTest(TestCase):
    @staticmethod
    def _fake_path(exists: bool):
        # 界面文件在 html/ 下：joinpath(EDITOR_DIR, EDITOR_FILE)
        return mock.Mock(joinpath=lambda *args: mock.Mock(exists=lambda: exists))

    def test_returns_none_when_pywebview_missing(self):
        with mock.patch.dict("sys.modules", {"webview": None}):
            self.assertIsNone(lyrics_editor.open_lyrics_editor())

    def test_returns_none_when_html_missing(self):
        fake_webview = mock.Mock()
        with mock.patch.dict("sys.modules", {"webview": fake_webview}), \
             mock.patch.object(lyrics_editor, "application_path", self._fake_path(False)):
            self.assertIsNone(lyrics_editor.open_lyrics_editor())
        fake_webview.create_window.assert_not_called()

    def test_returns_saved_lyrics(self):
        fake_webview = mock.Mock()

        def fake_start(func=None):
            # 模拟窗口内点「完成」：直接把结果写进 api
            api = fake_webview.create_window.call_args.kwargs["js_api"]
            api.save(json.dumps({"jap": "きみの", "chs": "你的", "useHover": True}))

        fake_webview.start.side_effect = fake_start
        with mock.patch.dict("sys.modules", {"webview": fake_webview}), \
             mock.patch.object(lyrics_editor, "application_path", self._fake_path(True)):
            lyrics = lyrics_editor.open_lyrics_editor("预填", use_hover=True)
        self.assertEqual("きみの", lyrics.lyrics_jap)
        self.assertEqual("你的", lyrics.lyrics_chs)
        self.assertTrue(lyrics.use_hover)
        context = fake_webview.create_window.call_args.kwargs["js_api"].get_context()
        self.assertEqual("预填", context["initial"])
        self.assertTrue(context["useHover"], "开关初始值要能传给界面")

    def test_returns_none_when_cancelled(self):
        fake_webview = mock.Mock()

        def fake_start(func=None):
            fake_webview.create_window.call_args.kwargs["js_api"].cancel()

        fake_webview.start.side_effect = fake_start
        with mock.patch.dict("sys.modules", {"webview": fake_webview}), \
             mock.patch.object(lyrics_editor, "application_path", self._fake_path(True)):
            self.assertIsNone(lyrics_editor.open_lyrics_editor())


class SongWiringTest(TestCase):
    def test_get_manual_lyrics_delegates_to_editor(self):
        import models.song
        with mock.patch.object(models.song, "open_lyrics_editor",
                               return_value=mock.Mock(lyrics_jap="きみの")) as editor:
            lyrics = models.song.get_manual_lyrics("预填", use_hover=True)
        self.assertEqual("きみの", lyrics.lyrics_jap)
        editor.assert_called_once_with("预填", use_hover=True)

    def test_get_manual_lyrics_defaults_hover_off(self):
        import models.song
        with mock.patch.object(models.song, "open_lyrics_editor",
                               return_value=mock.Mock(lyrics_jap="きみの")) as editor:
            models.song.get_manual_lyrics("预填")
        editor.assert_called_once_with("预填", use_hover=False)

    def test_get_manual_lyrics_returns_empty_on_cancel(self):
        import models.song
        with mock.patch.object(models.song, "open_lyrics_editor", return_value=None):
            lyrics = models.song.get_manual_lyrics()
        self.assertIsNone(lyrics.lyrics_jap)
        self.assertIsNone(lyrics.lyrics_chs)
